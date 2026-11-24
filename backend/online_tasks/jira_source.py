"""Async, read-only Jira Cloud source for online coding tasks."""
import os
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from executors.redaction import redact_secrets

_KEY = re.compile(r"[A-Z][A-Z0-9_]{0,49}-[0-9]{1,20}")


def issue_key(value: str) -> str:
    key = value.strip().upper()
    if not _KEY.fullmatch(key):
        raise ValueError("Expected a Jira issue key such as PAY-123")
    return key


class JiraSourceError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


def _text(value) -> str:
    """Read plain text or Jira's Atlassian Document Format."""
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return ""
    if value.get("type") == "text":
        return str(value.get("text") or "")
    if value.get("type") == "hardBreak":
        return "\n"
    separator = "\n" if value.get("type") in {"doc", "bulletList", "orderedList", "listItem"} else ""
    return separator.join(_text(node) for node in value.get("content", []))


@dataclass(frozen=True)
class JiraIssue:
    id: str
    key: str
    kind: str
    done: bool
    requirements: str


class JiraCloudSource:
    def __init__(self, site: str, email: str, token: str, *,
                 story_type: str = "Story", epic_type: str = "Epic",
                 acceptance_field: str = "", transport=None):
        parsed = urlsplit(site)
        if (parsed.scheme != "https" or not parsed.hostname or
                parsed.netloc != parsed.hostname or parsed.path not in {"", "/"} or
                parsed.query or parsed.fragment):
            raise ValueError("NUKE_JIRA_URL must be an HTTPS site origin")
        if acceptance_field and not re.fullmatch(r"customfield_[0-9]+", acceptance_field):
            raise ValueError("NUKE_JIRA_ACCEPTANCE_FIELD must be a Jira customfield ID")
        self.site = f"https://{parsed.hostname}"
        self.story_type = story_type.casefold()
        self.epic_type = epic_type.casefold()
        self.acceptance_field = acceptance_field
        self.fields = ["summary", "description", "issuetype", "status"]
        if acceptance_field:
            self.fields.append(acceptance_field)
        self._client = httpx.AsyncClient(
            base_url=self.site,
            auth=(email, token),
            headers={"Accept": "application/json"},
            timeout=20,
            transport=transport,
        )

    @classmethod
    def from_environment(cls):
        site, email, token = (os.getenv(f"NUKE_JIRA_{name}", "").strip()
                              for name in ("URL", "EMAIL", "API_TOKEN"))
        if not all((site, email, token)):
            return None
        return cls(site, email, token,
                   story_type=os.getenv("NUKE_JIRA_STORY_TYPE", "Story"),
                   epic_type=os.getenv("NUKE_JIRA_EPIC_TYPE", "Epic"),
                   acceptance_field=os.getenv("NUKE_JIRA_ACCEPTANCE_FIELD", ""))

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.RequestError:
            raise JiraSourceError("Jira connection failed", retryable=True) from None
        if response.is_error or response.is_redirect:
            code = response.status_code
            raise JiraSourceError(f"Jira returned HTTP {code}", retryable=code == 429 or code >= 500)
        try:
            result = response.json()
        except ValueError:
            raise JiraSourceError("Jira returned invalid JSON") from None
        if not isinstance(result, dict):
            raise JiraSourceError("Jira returned an unexpected response")
        return result

    def _issue(self, raw: dict) -> JiraIssue:
        try:
            key = issue_key(raw["key"])
            stable_id = str(raw["id"])
            if not stable_id.isdecimal():
                raise ValueError("invalid issue ID")
            fields = raw["fields"]
            kind = fields["issuetype"]["name"].casefold()
            done = fields["status"]["statusCategory"]["key"] == "done"
            summary = _text(fields.get("summary")).strip()
            description = _text(fields.get("description")).strip()
            acceptance = _text(fields.get(self.acceptance_field)).strip()
        except (KeyError, TypeError, ValueError, AttributeError):
            raise JiraSourceError("Jira issue is missing required fields") from None
        requirements = f"Jira {key}: {summary}\n\n{description}" if summary and description else ""
        if acceptance and requirements:
            requirements += f"\n\nAcceptance criteria\n{acceptance}"
        requirements, _ = redact_secrets(requirements)
        if len(requirements) > 10000:
            requirements = ""
        return JiraIssue(stable_id, key, kind, done, requirements)

    async def get(self, key: str) -> JiraIssue:
        result = await self._request("GET", f"/rest/api/3/issue/{issue_key(key)}",
                                     params={"fields": ",".join(self.fields)})
        return self._issue(result)

    async def children(self, epic_key: str, cursor: str | None) -> tuple[list[JiraIssue], str | None]:
        query = {"jql": f'parent = "{issue_key(epic_key)}" ORDER BY key ASC',
                 "fields": self.fields, "maxResults": 50}
        if cursor:
            query["nextPageToken"] = cursor
        result = await self._request("POST", "/rest/api/3/search/jql", json=query)
        if not isinstance(result.get("issues"), list):
            raise JiraSourceError("Jira search returned an invalid issue page")
        next_cursor = result.get("nextPageToken")
        if (next_cursor is not None and (not isinstance(next_cursor, str) or next_cursor == cursor)
                or not next_cursor and result.get("isLast") is not True):
            raise JiraSourceError("Jira search did not provide a valid continuation")
        issues = [self._issue(raw) for raw in result["issues"]]
        return [issue for issue in issues if issue.kind == self.story_type], next_cursor or None
