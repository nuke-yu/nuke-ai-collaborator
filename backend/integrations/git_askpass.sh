#!/bin/sh

# Git invokes askpass with prompts containing the credential URL. Never release
# the token when URL rewriting or local configuration redirects to another host.
case "$1" in
  *"//github.com/"*|*"//github.com'"*|*"//github.com:"*|\
  *"@github.com/"*|*"@github.com'"*|*"@github.com:"*) ;;
  *) exit 1 ;;
esac

case "$1" in
  *Username*) printf '%s\n' "x-access-token" ;;
  *) printf '%s\n' "${GITHUB_TOKEN:-${GH_TOKEN:-}}" ;;
esac
