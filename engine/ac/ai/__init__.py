"""Optional AI layer (any OpenAI-compatible provider; default a local proxy at 127.0.0.1:8168): client (HTTP, circuit breaker, JSON repair),
prompts (task prompts + validation + rule fallbacks), tasks (cached one-call entry points), text (display
words, sentence units, mm:ss helpers). Every feature must keep working when the proxy is down."""
