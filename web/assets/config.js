/* API base. Empty means same origin (the CloudFront site proxies /api and /v1). */
window.CS_API_BASE = (location.protocol === "file:" || location.hostname === "localhost" || location.hostname === "127.0.0.1")
  ? (window.CS_REMOTE_API || "") : "";
