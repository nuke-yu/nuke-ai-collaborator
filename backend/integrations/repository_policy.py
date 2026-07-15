"""Repository URL admission policies for coding-agent integrations.

Admission is intentionally separate from clone/auth/PR execution. Registering a
host here is safe only after a matching integration provides host-scoped
credentials and the full repository lifecycle for that provider.
"""
from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit


_REPOSITORY_SEGMENT_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
)


@dataclass(frozen=True)
class HostedGitProvider:
    """URL rules for one hosted Git provider."""

    name: str
    hosts: tuple[str, ...]
    min_path_segments: int = 2
    max_path_segments: int | None = 2

    def validate_path(self, parsed: SplitResult) -> None:
        segments = parsed.path.removeprefix("/").split("/")
        if (
            len(segments) < self.min_path_segments
            or (
                self.max_path_segments is not None
                and len(segments) > self.max_path_segments
            )
            or not segments[-1].endswith(".git")
        ):
            raise ValueError(
                f"repo_url must identify a valid {self.name} repository ending in .git"
            )

        repository_name = segments[-1][:-4]
        normalized_segments = [*segments[:-1], repository_name]
        if any(
            not segment
            or segment in {".", ".."}
            or any(char not in _REPOSITORY_SEGMENT_CHARS for char in segment)
            for segment in normalized_segments
        ):
            raise ValueError(f"repo_url contains an invalid {self.name} repository path")


class RepositoryAdmissionPolicy:
    """Resolve repository URLs against an explicit provider registry."""

    def __init__(self, providers: tuple[HostedGitProvider, ...]):
        if not providers:
            raise ValueError("At least one repository provider is required")
        self._providers_by_host: dict[str, HostedGitProvider] = {}
        for provider in providers:
            if not provider.name or not provider.hosts:
                raise ValueError("Repository providers require a name and at least one host")
            for host in provider.hosts:
                normalized_host = host.lower()
                if normalized_host in self._providers_by_host:
                    raise ValueError(f"Duplicate repository provider host: {host}")
                self._providers_by_host[normalized_host] = provider

    @property
    def supported_hosts(self) -> tuple[str, ...]:
        return tuple(sorted(self._providers_by_host))

    def validate(self, value: str) -> str:
        """Validate an HTTPS, credential-free URL for a registered provider."""
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError as exc:
            raise ValueError("repo_url must be a valid HTTPS git URL") from exc

        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("repo_url must be an HTTPS git URL")
        if (
            parsed.username is not None
            or parsed.password is not None
            or port is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "repo_url must not contain credentials, a custom port, query, or fragment"
            )

        host = parsed.hostname.lower()
        provider = self._providers_by_host.get(host)
        if provider is None:
            supported = ", ".join(self.supported_hosts)
            raise ValueError(
                f"repository host {host} is not yet supported; supported hosts: {supported}"
            )

        provider.validate_path(parsed)
        return value


GITHUB_PROVIDER = HostedGitProvider(name="GitHub", hosts=("github.com",))

# Keep this fail-closed until each additional provider has host-scoped auth,
# preflight, clone/push, and pull-request implementations.
DEFAULT_REPOSITORY_ADMISSION_POLICY = RepositoryAdmissionPolicy((GITHUB_PROVIDER,))
