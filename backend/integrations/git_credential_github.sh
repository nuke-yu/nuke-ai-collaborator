#!/bin/sh

# Git credential helpers receive structured key/value records on stdin. Only
# release the token for the exact GitHub HTTPS authority; never parse prompts.
[ "${1:-}" = "get" ] || exit 0

protocol=
host=
username=

while IFS= read -r line && [ -n "$line" ]; do
  key=${line%%=*}
  value=${line#*=}
  case "$key" in
    protocol) protocol=$value ;;
    host) host=$value ;;
    username) username=$value ;;
  esac
done

[ "$protocol" = "https" ] || exit 0
[ "$host" = "github.com" ] || exit 0
[ -z "$username" ] || exit 0

token=${GITHUB_TOKEN:-${GH_TOKEN:-}}
[ -n "$token" ] || exit 1

printf 'username=x-access-token\n'
printf 'password=%s\n' "$token"
