# spectral-python

Python client for the Spectral sidecar.

Every service instance runs next to a Spectral sidecar. Your service uses this client to talk to its own sidecar over HTTP: register itself, keep its lease alive, and look up other services. The sidecar handles everything else (gossip, failure detection, keeping the directory in sync across machines).

## Install

```sh
pip install spectral-sdk
```

Python 3.9+. No dependencies outside the standard library. The import name is `spectral_sdk`, because the `spectral` name on PyPI is already taken by an unrelated package.

## Quickstart

```python
import spectral_sdk as spectral

client = spectral.Client()

lease = client.register(
    service="payments",
    instance_id="payments-1",
    endpoint="http://100.64.1.3:9007",
    ttl=10,
)

with client.keep_alive_in_background(lease):
    try:
        inventory = client.lookup("inventory")
        requests.get(inventory.endpoint + "/stock")
    except spectral.ServiceNotFoundError:
        pass
```

A runnable version is in [`examples/quickstart.py`](examples/quickstart.py).

## Connecting to the sidecar

`spectral.Client(addr=None, timeout=10.0)` picks the sidecar address in this order:

1. the `addr` argument, if given
2. the `SPECTRAL_ADDR` environment variable
3. `http://127.0.0.1:8000`, the sidecar's default API port

`timeout` is in seconds and applies to each request. Proxy environment variables are ignored, since the sidecar always runs next to your service.

## API

| Method | What it does |
|---|---|
| `register(service, instance_id, endpoint, ttl) -> Lease` | Announce this instance. `ttl` is in seconds. The sidecar spreads it to the whole network. |
| `renew(token)` | Extend the lease once. |
| `keep_alive(lease, stop=None)` | Renew every `ttl / 3` until the `threading.Event` `stop` is set. Blocks. Retries failed renewals and raises `LeaseExpiredError` if none succeed for a full TTL, or right away if the sidecar no longer knows the lease. |
| `keep_alive_in_background(lease) -> KeepAliveThread` | Same as `keep_alive`, on a daemon thread. Call `.stop()` or use it as a context manager. If it gives up, the exception is in `.error`. |
| `lookup(service) -> Provider` | Get one healthy instance of a service, answered from the sidecar's local copy of the directory. |
| `status() -> Status` | Whether the sidecar is ready, and its state. |

`Lease`, `Provider` and `Status` are frozen dataclasses. `Lease.ttl` is in seconds and `Lease.expires_at` is a timezone-aware `datetime`.

If your instance stops renewing (crash, hang, keep-alive stopped), the lease expires and the instance disappears from every sidecar's directory. There is no explicit deregister: stop renewing, or shut the sidecar down.

## Errors

Every exception derives from `SpectralError`:

| Exception | Meaning |
|---|---|
| `InvalidRegistrationError` | The sidecar rejected the registration (missing fields, bad endpoint URL, non-positive TTL). |
| `AlreadyRegisteredError` | This registration already exists. |
| `ServiceNotFoundError` | No healthy instance of that service is known. |
| `LeaseExpiredError` | The lease is gone; register again. |
| `SidecarTimeoutError` | The sidecar accepted the request but did not answer in time. |
| `SidecarUnavailableError` | The sidecar could not be reached at all. |
| `APIError` | Any other non-2xx response. Base class of the four HTTP errors above; has `op`, `status_code` and `message`. |

## Development

```sh
PYTHONPATH=src:tests python -m unittest discover -s tests
```

## HTTP contract

This is the sidecar API the client speaks. Every Spectral SDK uses the same contract.

| Call | Request | Success | Errors |
|---|---|---|---|
| `POST /register` | `{"service", "instance_id", "endpoint", "ttl": "10s"}` | `201 {"token", "ttl", "expires_at"}` | `400` invalid, `409` exists, `504` timeout |
| `POST /renew` | `{"token"}` | `202` | `404` unknown or expired lease, `504` timeout |
| `GET /lookup?service=<name>` | | `200 {"service", "instance_id", "endpoint"}` | `404` no provider, `504` timeout |
| `GET /status` | | `200 {"ready", "state"}` | `504` timeout |

Durations are Go duration strings (`"10s"`, `"1m30s"`). Timestamps are RFC 3339. Error bodies are plain text.
