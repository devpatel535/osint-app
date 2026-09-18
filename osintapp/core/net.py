"""HTTP plumbing for the probes.

Two things here are load-bearing:

* ``requests`` is preferred but optional. A user who unzips the source and
  double-clicks ``run.py`` on a stock Python install has no site-packages, and
  an OSINT tool that refuses to start is useless - so there is a urllib
  fallback that produces the same ``Response`` object.
* Response bodies are read with a hard byte cap. We only need the first few KB
  to match an error string or an OpenGraph tag, and some of the sites in the
  list stream megabytes. Capping keeps a 260-site sweep from pulling down
  hundreds of MB.
"""

from __future__ import annotations

import random
import re
import ssl
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from ..paths import resource_path

try:  # pragma: no cover - availability differs per install
    import requests

    HAVE_REQUESTS = True
except Exception:  # noqa: BLE001 - any import failure means "use the fallback"
    requests = None  # type: ignore[assignment]
    HAVE_REQUESTS = False

import urllib.error
import urllib.request

MAX_BODY_BYTES = 262_144  # 256 KB is plenty for a <head> plus a bit of body

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_ua_cache: Optional[List[str]] = None
_ua_lock = threading.Lock()


def user_agents() -> List[str]:
    """Load (once) the rotating User-Agent pool shipped with the app."""
    global _ua_cache
    with _ua_lock:
        if _ua_cache is not None:
            return _ua_cache
        pool: List[str] = []
        try:
            with open(resource_path("user_agents.txt"), "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        pool.append(line)
        except OSError:
            pass
        _ua_cache = pool or [_DEFAULT_UA]
        return _ua_cache


def random_user_agent() -> str:
    return random.choice(user_agents())


@dataclass
class Response:
    """Uniform result shape from either backend."""

    url: str
    final_url: str = ""
    status: Optional[int] = None
    body: str = ""
    error: str = ""
    headers: Dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error == "" and self.status is not None

    @property
    def redirected(self) -> bool:
        return bool(self.final_url) and self.final_url.rstrip("/") != self.url.rstrip("/")


def _decode(raw: bytes, content_type: str) -> str:
    match = re.search(r"charset=([\w\-]+)", content_type or "", re.I)
    if match:
        try:
            return raw.decode(match.group(1), errors="replace")
        except LookupError:
            pass
    return raw.decode("utf-8", errors="replace")


class Fetcher:
    """Thread-safe HTTP client used by every scanner.

    One instance per search. ``requests.Session`` is thread-safe enough for
    concurrent GETs sharing a connection pool, which is what makes a 260-site
    sweep finish in seconds rather than minutes.
    """

    def __init__(
        self,
        timeout: float = 8.0,
        proxy: str = "",
        verify_tls: bool = True,
        rotate_user_agent: bool = True,
        pool_size: int = 32,
    ):
        self.timeout = float(timeout)
        # Connect and read are budgeted separately. A host that is dead,
        # firewalled or DNS-black-holed shows up as a failed TCP/TLS handshake,
        # and waiting the full read budget for that is pure dead time - on a
        # 243-site sweep the unreachable ones dominate the tail. Connect gets a
        # short, fixed ceiling; slow-but-alive servers still get the full read.
        self.connect_timeout = min(4.0, self.timeout)
        self.proxy = (proxy or "").strip()
        self.verify_tls = bool(verify_tls)
        self.rotate_user_agent = rotate_user_agent
        self._session = None

        if HAVE_REQUESTS:
            self._session = requests.Session()
            if self.proxy:
                self._session.proxies.update({"http": self.proxy, "https": self.proxy})

            # The default adapter caches 10 host pools with 10 connections each.
            # A sweep touches ~243 *distinct* hosts from N threads at once, so
            # with the defaults urllib3 evicts a pool on nearly every probe and
            # re-does the TLS handshake. Sizing both to the worker count is the
            # single biggest win available here.
            pool = max(16, int(pool_size))
            adapter = requests.adapters.HTTPAdapter(
                pool_connections=pool,
                pool_maxsize=pool,
                max_retries=0,       # a retry on a 243-site sweep just doubles the tail
                pool_block=False,
            )
            self._session.mount("https://", adapter)
            self._session.mount("http://", adapter)

            # A profile URL that needs more than a handful of hops is a redirect
            # loop or a tracking chain, not a profile. The default of 30 lets
            # those burn the whole timeout budget.
            self._session.max_redirects = 6

            # Static headers live on the session so they are not rebuilt per
            # request; only the User-Agent varies. Leaving Accept-Encoding to
            # requests keeps gzip/deflate negotiation intact.
            self._session.headers.update({
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Cache-Control": "no-cache",
            })

    # -- headers ---------------------------------------------------------
    def _headers(self) -> Dict[str, str]:
        """Per-request headers.

        With the requests backend the static headers already sit on the
        session, so this only carries the rotating User-Agent. The urllib
        fallback has no session, so it gets the full set.
        """
        agent = random_user_agent() if self.rotate_user_agent else _DEFAULT_UA
        if self._session is not None:
            return {"User-Agent": agent}
        return {
            "User-Agent": agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Cache-Control": "no-cache",
        }

    # -- public ----------------------------------------------------------
    def get(self, url: str, want_body: bool = True, error_body: bool = True) -> Response:
        """Fetch *url*.

        ``error_body=False`` streams nothing when the server answers 4xx/5xx.
        A probe treats any such status as "no account" without inspecting the
        page, so downloading the site's (often heavy) error page is wasted
        bandwidth and time on the majority of a sweep's responses.
        """
        if HAVE_REQUESTS:
            return self._get_requests(url, want_body, error_body)
        return self._get_urllib(url, want_body, error_body)

    def get_json(self, url: str):
        """GET and parse JSON, or return None. Used for Gravatar / HIBP."""
        import json

        response = self.get(url)
        if not response.ok or response.status != 200 or not response.body:
            return None
        try:
            return json.loads(response.body)
        except (ValueError, TypeError):
            return None

    def close(self) -> None:
        if self._session is not None:
            try:
                self._session.close()
            except Exception:  # noqa: BLE001
                pass

    def __enter__(self) -> "Fetcher":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # -- backends --------------------------------------------------------
    def _get_requests(self, url: str, want_body: bool, error_body: bool = True) -> Response:
        try:
            response = self._session.get(  # type: ignore[union-attr]
                url,
                headers=self._headers(),
                timeout=(self.connect_timeout, self.timeout),
                allow_redirects=True,
                stream=True,
                verify=self.verify_tls,
            )
        except Exception as exc:  # noqa: BLE001 - requests raises a wide family
            return Response(url=url, error=_short_error(exc))

        try:
            body = ""
            if want_body and (error_body or response.status_code < 400):
                chunks, total = [], 0
                for chunk in response.iter_content(16_384):
                    if not chunk:
                        continue
                    chunks.append(chunk)
                    total += len(chunk)
                    if total >= MAX_BODY_BYTES:
                        break
                body = _decode(b"".join(chunks), response.headers.get("Content-Type", ""))
            return Response(
                url=url,
                final_url=str(response.url),
                status=response.status_code,
                body=body,
                headers={k.lower(): v for k, v in response.headers.items()},
            )
        except Exception as exc:  # noqa: BLE001 - mid-stream read failures
            return Response(
                url=url,
                final_url=str(getattr(response, "url", "")),
                status=response.status_code,
                error=_short_error(exc),
            )
        finally:
            response.close()

    def _get_urllib(self, url: str, want_body: bool, error_body: bool = True) -> Response:
        context = None
        if not self.verify_tls:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE

        opener_handlers = []
        if self.proxy:
            opener_handlers.append(
                urllib.request.ProxyHandler({"http": self.proxy, "https": self.proxy})
            )
        if context is not None:
            opener_handlers.append(urllib.request.HTTPSHandler(context=context))
        opener = urllib.request.build_opener(*opener_handlers)

        request = urllib.request.Request(url, headers=self._headers())
        try:
            with opener.open(request, timeout=self.timeout) as handle:
                raw = handle.read(MAX_BODY_BYTES) if want_body else b""
                return Response(
                    url=url,
                    final_url=handle.geturl(),
                    status=handle.status,
                    body=_decode(raw, handle.headers.get("Content-Type", "")),
                    headers={k.lower(): v for k, v in handle.headers.items()},
                )
        except urllib.error.HTTPError as exc:
            # A 404 is a perfectly good answer, not a failure - keep the status.
            raw = b""
            try:
                raw = exc.read(MAX_BODY_BYTES) if (want_body and error_body) else b""
            except Exception:  # noqa: BLE001
                pass
            return Response(
                url=url,
                final_url=url,
                status=exc.code,
                body=_decode(raw, exc.headers.get("Content-Type", "") if exc.headers else ""),
            )
        except Exception as exc:  # noqa: BLE001
            return Response(url=url, error=_short_error(exc))


# Exception class name -> the phrase shown to the user. Deliberately keyed on
# the type rather than the message: requests embeds the failing host in its
# message text, which would make every site's failure look unique and defeat
# the "234 probes failed for the same reason" grouping in the report.
_ERROR_PHRASES = {
    "ConnectTimeout": "connection timed out",
    "ReadTimeout": "read timed out",
    "ReadTimeoutError": "read timed out",
    "Timeout": "timed out",
    "timeout": "timed out",
    "TimeoutError": "timed out",
    "SSLError": "TLS/certificate error",
    "SSLCertVerificationError": "TLS/certificate error",
    "ProxyError": "proxy refused the connection",
    "NewConnectionError": "could not open a connection",
    "MaxRetryError": "connection failed after retries",
    "NameResolutionError": "host name did not resolve",
    "ConnectionResetError": "connection reset by the server",
    "ChunkedEncodingError": "connection dropped mid-response",
    "ContentDecodingError": "bad content encoding",
    "TooManyRedirects": "too many redirects",
    "InvalidURL": "malformed URL",
    "LocationParseError": "malformed URL",
    "ConnectionError": "connection failed",
    "URLError": "connection failed",
    "OSError": "network error",
}


def _short_error(exc: BaseException) -> str:
    """Compact, human-readable reason - full tracebacks are noise in a report."""
    name = type(exc).__name__
    if name in _ERROR_PHRASES:
        return _ERROR_PHRASES[name]

    # Walk the inheritance chain, so an unfamiliar subclass still lands on the
    # right phrase (requests.exceptions.ProxyError -> ConnectionError -> ...).
    for base in type(exc).__mro__[1:]:
        phrase = _ERROR_PHRASES.get(base.__name__)
        if phrase:
            return phrase

    text = str(exc).strip()
    return f"{name}: {text[:100]}" if text else name

