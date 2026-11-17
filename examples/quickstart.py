import signal
import threading

import spectral_sdk as spectral


def main() -> None:
    client = spectral.Client()

    lease = client.register(
        service="payments",
        instance_id="payments-1",
        endpoint="http://127.0.0.1:9000",
        ttl=10,
    )
    print(f"registered with {client.addr}, lease expires at {lease.expires_at}")

    shutdown = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: shutdown.set())
    signal.signal(signal.SIGTERM, lambda *_: shutdown.set())

    with client.keep_alive_in_background(lease) as keeper:
        try:
            inventory = client.lookup("inventory")
            print(f"inventory is at {inventory.endpoint}")
        except spectral.ServiceNotFoundError:
            print("no inventory instance registered yet")

        while not shutdown.wait(1):
            if keeper.error is not None:
                print(f"keepalive stopped: {keeper.error}")
                break


if __name__ == "__main__":
    main()
