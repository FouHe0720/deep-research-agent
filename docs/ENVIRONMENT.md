# Environment preflight

Execution: WSL Linux; parent workspace confirmed outside Git. Python 3.12.3; Git 2.43.0; GitHub CLI 2.45.0; Docker CLI 29.8.2; Node 24.21.0. GitHub CLI active account matched requested owner. No system package installation needed. Docker daemon/build and remote CI are verified separately at delivery. No sibling repository is modified.

V0 verification: offline demo produced six fixture sources and valid citation bindings; 3 tests passed before the V0 commit. V1 actual network smoke: unauthenticated GitHub repository search through SafeHTTP returned HTTP 200 and one public repository result, with exactly one outbound request. This does not establish arXiv, Brave or LLM availability. The uv-managed project interpreter is Python 3.12.15; the initial system interpreter was 3.12.3.
