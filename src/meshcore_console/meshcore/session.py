from __future__ import annotations

import asyncio
import gc
import inspect
import logging
import queue
import random
import threading
import time
from typing import Any, AsyncIterator, Callable

logger = logging.getLogger(__name__)

from meshcore_console.core.types import (
    EventServiceProtocol,
    EventSubscriberProtocol,
    LocalIdentityProtocol,
    LoggerCallback,
    MeshEventDict,
    MeshNodeProtocol,
    SendResultDict,
    SessionStatusDict,
    SX1262RadioProtocol,
)

from .cayenne_lpp import encode_telemetry
from . import cayenne_lpp as _cayenne_lpp_mod

# openhop_core's ProtocolResponseHandler tries ``from utils.cayenne_lpp_helpers
# import decode_cayenne_lpp_payload`` which isn't shipped.  Register our
# module under that name so the import succeeds.
import sys as _sys
import types as _types

if "utils.cayenne_lpp_helpers" not in _sys.modules:
    _shim = _types.ModuleType("utils.cayenne_lpp_helpers")
    _shim.decode_cayenne_lpp_payload = _cayenne_lpp_mod.decode_cayenne_lpp_payload  # type: ignore[attr-defined]
    _sys.modules.setdefault("utils", _types.ModuleType("utils"))
    _sys.modules["utils.cayenne_lpp_helpers"] = _shim

from .channel_db import ChannelDatabase
from .config import RuntimeRadioConfig, load_hardware_config_from_env
from .contact_book import Contact, ContactBook
from .db import open_db
from .discovery import (
    NODE_TYPE_CHAT,
    AnswerLimiter,
    decode_owner_reply,
    discover,
    wants_node_type,
)
from .event_bridge import attach_dispatcher_callbacks, attach_event_service_subscriber
from .operations import (
    request_owner_info,
    request_repeater_data,
    request_telemetry,
    send_advert,
    send_group_text,
    send_login,
    send_logout,
    send_repeater_command,
    send_text,
)
from .region import normalize_region_scope, region_transport_key
from .runtime import create_mesh_node, create_radio, import_openhop_core


class OpenHopCoreSession:
    """Async wrapper around openhop_core MeshNode lifecycle."""

    def __init__(self, config: RuntimeRadioConfig, logger: LoggerCallback | None = None) -> None:
        self.config = config
        self._logger = logger
        self._radio: SX1262RadioProtocol | None = None
        self._identity: LocalIdentityProtocol | None = None
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._raw_waiters: dict[bytes, Callable[[bytes], None]] = {}
        self._last_request_ts = 0
        self._node: MeshNodeProtocol | None = None
        self._event_service: EventServiceProtocol | None = None
        self._event_subscriber: EventSubscriberProtocol | None = None
        self._node_task: asyncio.Task[None] | None = None
        self._event_queue: queue.Queue[MeshEventDict] = queue.Queue()
        self._event_notify: Callable[[], None] | None = None
        self._telemetry_data_fn: Callable[[], dict[str, Any]] | None = None
        self._is_favorite_fn: Callable[[str], bool] | None = None
        self._db = open_db()
        self._channel_db = ChannelDatabase(self._db)
        self._contact_book = ContactBook()
        self._hw_thread_ids: frozenset[int] = frozenset()

    def _log(self, message: str) -> None:
        if self._logger is not None:
            self._logger(message)
        else:
            logger.debug(message)

    def set_event_notify(self, notify_fn: Callable[[], None]) -> None:
        self._event_notify = notify_fn

    def set_telemetry_data_fn(self, fn: Callable[[], dict[str, Any]]) -> None:
        """Set the callback that provides local telemetry data for inbound requests."""
        self._telemetry_data_fn = fn

    def set_is_favorite_fn(self, fn: Callable[[str], bool]) -> None:
        """Set a callback that checks whether a public key hex belongs to a favorite peer."""
        self._is_favorite_fn = fn

    def _emit(self, payload: MeshEventDict) -> None:
        self._event_queue.put_nowait(payload)
        if self._event_notify is not None:
            try:
                self._event_notify()
            except Exception:  # noqa: BLE001
                pass  # Must never crash the radio driver

    def _register_discovery_handler(self) -> None:
        """Register a handler that responds to incoming discovery requests.

        When another node sends a CONTROL discovery request for chat nodes, we
        reply with our public key and node type so they can discover us, at
        most 4 times per 2 minutes (like firmware repeaters) and after a random
        delay so simultaneous answerers don't collide.
        """
        from openhop_core.protocol.constants import ADVERT_FLAG_IS_CHAT_NODE
        from openhop_core.protocol.packet_builder import PacketBuilder

        assert self._node is not None
        assert self._identity is not None

        dispatcher = self._node.dispatcher
        control_handler = dispatcher.control_handler

        # Capture identity ref for the closure
        identity = self._identity
        limiter = AnswerLimiter()

        def _on_discovery_request(request_data: dict) -> None:
            if not wants_node_type(request_data.get("filter", 0), NODE_TYPE_CHAT):
                return  # e.g. a repeater's discover.neighbors, which asks for repeaters
            if not limiter.allow():
                self._log("discovery request not answered (rate limit)")
                return
            tag = request_data.get("tag", 0)
            prefix_only = request_data.get("prefix_only", False)
            inbound_snr = request_data.get("snr", 0.0)

            # Our Ed25519 identity key (what adverts carry), not the X25519
            # get_shared_public_key() ECDH key.
            pub_key = identity.get_public_key()

            response = PacketBuilder.create_discovery_response(
                tag=tag,
                node_type=ADVERT_FLAG_IS_CHAT_NODE,
                inbound_snr=inbound_snr,
                pub_key=pub_key,
                prefix_only=prefix_only,
            )

            async def _send() -> None:
                await asyncio.sleep(random.uniform(0.2, 2.0))
                try:
                    await dispatcher.send_packet(response, wait_for_ack=False)
                    self._log(f"sent discovery response for tag=0x{tag:08X}")
                except Exception as exc:
                    self._log(f"failed to send discovery response: {exc}")

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return  # No running loop — skip
            task = loop.create_task(_send())
            self._background_tasks.add(task)  # keep a reference until it is done
            task.add_done_callback(self._background_tasks.discard)

        control_handler.set_request_callback(_on_discovery_request)

    def _build_telemetry_handler(self) -> Callable[..., bytes | None]:
        """Build a handler closure for inbound telemetry requests (REQ type 0x03)."""
        TELEM_PERM_BASE = 0x01  # noqa: N806
        TELEM_PERM_LOCATION = 0x02  # noqa: N806

        def _handle_telemetry(client: Any, _timestamp: int, req_data: bytes) -> bytes | None:
            if self._telemetry_data_fn is None:
                return None
            data = self._telemetry_data_fn()
            if not data.get("allow"):
                self._log("telemetry request denied (allow_telemetry=False)")
                return None
            if data.get("favorites_only") and self._is_favorite_fn is not None:
                if not client or not self._is_favorite_fn(client.public_key):
                    sender = client.name if client else "unknown"
                    self._log(f"telemetry request denied (not a favorite: {sender})")
                    return None
            mask = req_data[0] if req_data else 0x00
            include_base = not (mask & TELEM_PERM_BASE)
            include_location = not (mask & TELEM_PERM_LOCATION)
            lat = data.get("lat") if include_location else None
            lon = data.get("lon") if include_location else None
            voltage = data.get("voltage") if include_base else None
            payload = encode_telemetry(
                channel=1,
                lat=lat,
                lon=lon,
                voltage=voltage,
            )
            if not payload:
                self._log("telemetry request: no sensor data available")
                return None
            self._log(
                f"responding to telemetry request (voltage={voltage}, location={lat is not None})"
            )
            return payload

        return _handle_telemetry

    def _register_req_handler(self) -> None:
        """Register a handler for incoming REQ packets.

        openhop_core's register_default_handlers() does not register a handler
        for PAYLOAD_TYPE_REQ (0x00), so incoming requests are silently
        dropped.  We register ProtocolRequestHandler here and wrap it so
        that any generated RESPONSE packet is actually transmitted.

        ANON_REQ (0x07) stays with openhop_core's own handler: in an anonymous
        request 0x03 is ANON_REQ_TYPE_BASIC (clock), not a telemetry request,
        and telemetry is only for senders already in the contact book.
        """
        import struct

        from openhop_core.node.handlers.protocol_request import ProtocolRequestHandler
        from openhop_core.protocol import CryptoUtils, Identity
        from openhop_core.protocol.constants import (
            PAYLOAD_TYPE_REQ,
        )

        REQ_TYPE_GET_TELEMETRY_DATA = 0x03  # noqa: N806

        assert self._node is not None
        assert self._identity is not None

        telemetry_handler = self._build_telemetry_handler()
        request_handlers = {REQ_TYPE_GET_TELEMETRY_DATA: telemetry_handler}

        req_handler = ProtocolRequestHandler(
            local_identity=self._identity,
            contacts=self._contact_book,
            log_fn=self._log,
            request_handlers=request_handlers,
        )

        dispatcher = self._node.dispatcher

        identity = self._identity
        contact_book = self._contact_book
        our_pubkey = identity.get_public_key()
        our_hash = our_pubkey[0]
        our_privkey = identity.get_private_key()

        last_request_ts: dict[str, int] = {}  # newest request timestamp per contact key

        async def _handle_req(pkt: Any) -> None:
            # ProtocolRequestHandler._get_client matches on first byte only,
            # which collides when multiple contacts share a hash prefix.
            # Try each matching contact until decryption succeeds.
            if len(pkt.payload) < 2:
                return
            dest_hash = pkt.payload[0]
            src_hash = pkt.payload[1]
            if dest_hash != our_hash:
                return

            encrypted_data = bytes(pkt.payload[2:])
            candidates = [
                c
                for c in contact_book.contacts
                if c.public_key and bytes.fromhex(c.public_key)[0] == src_hash
            ]
            if not candidates:
                self._log(f"REQ from unknown 0x{src_hash:02X}")
                return

            for contact in candidates:
                # Key handling inside the try: one contact with an unusable
                # stored key used to abort the request for every contact
                # sharing its hash byte.
                try:
                    peer_id = Identity(bytes.fromhex(contact.public_key))
                    ss = peer_id.calc_shared_secret(our_privkey)
                    plaintext = CryptoUtils.mac_then_decrypt(
                        ss[:16],
                        ss,
                        encrypted_data,
                    )
                except Exception:
                    continue

                if len(plaintext) < 5:
                    continue
                timestamp = struct.unpack("<I", plaintext[0:4])[0]
                req_type = plaintext[4]
                req_data = plaintext[5:] if len(plaintext) > 5 else b""
                # Like firmware's per-client last_timestamp: a captured request
                # replayed later would otherwise be answered (and transmitted
                # for) again.
                if timestamp <= last_request_ts.get(contact.public_key, -1):
                    self._log(f"REQ from {contact.name}: replayed (ts={timestamp}), ignoring")
                    return
                last_request_ts[contact.public_key] = timestamp
                self._log(
                    f"REQ from {contact.name} (0x{src_hash:02X}) "
                    f"type=0x{req_type:02X} ts={timestamp}"
                )

                handler_fn = request_handlers.get(req_type)
                if handler_fn is None:
                    self._log(f"REQ: no handler for type 0x{req_type:02X}")
                    return

                response_payload = handler_fn(contact, timestamp, req_data)
                if response_payload is None:
                    self._log("REQ: handler returned no data")
                    return

                response_pkt = req_handler._build_response(
                    pkt,
                    contact,
                    struct.pack("<I", timestamp) + response_payload,
                    ss,
                )
                if response_pkt is not None:
                    logger.debug(
                        "REQ handler: sending response (%d bytes)",
                        len(response_pkt.payload),
                    )
                    await dispatcher.send_packet(response_pkt, wait_for_ack=False)
                return

            self._log(
                f"REQ from 0x{src_hash:02X}: HMAC failed for all {len(candidates)} candidate(s)"
            )

        # local_delivery: like openhop_core's own payload handlers, so direct
        # packets still in transit (hops left on their path) are not consumed.
        dispatcher.register_handler(PAYLOAD_TYPE_REQ, _handle_req, local_delivery=True)

    async def _call_maybe_async(self, target: Any, name: str) -> None:
        fn = getattr(target, name, None)
        if not callable(fn):
            return
        try:
            result = fn()
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            logger.debug("cleanup %s.%s() failed: %s", type(target).__name__, name, exc)

    async def _poll_hw_threads(self, timeout: float = 5.0) -> None:
        """Wait for hardware threads spawned during start() to exit.

        openhop_core's GPIOPinManager creates OS threads for edge detection and
        IRQ handling.  These threads hold GPIO line file descriptors; the
        kernel only releases the lines once the threads (and their fds) are
        gone.

        Even after threads exit, the kernel GPIO line release is asynchronous
        (the fd close propagates through the gpiochip driver), so we add a
        post-exit settle delay.
        """
        if not self._hw_thread_ids:
            self._log("no hardware threads tracked; using fallback GPIO settle delay")
            await asyncio.sleep(1.0)
            return
        self._log(f"waiting for {len(self._hw_thread_ids)} hardware thread(s) to exit")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            alive = {t.ident for t in threading.enumerate() if t.is_alive()}
            remaining = self._hw_thread_ids & alive
            if not remaining:
                elapsed = timeout - (deadline - time.monotonic())
                self._log(f"hardware threads exited after {elapsed:.1f}s")
                break
            await asyncio.sleep(0.1)
        else:
            self._log(f"timeout waiting for {len(remaining)} hardware thread(s) to exit")
        self._hw_thread_ids = frozenset()
        # Kernel GPIO line release is asynchronous — the gpiochip driver
        # needs time after userspace fds close to mark lines as free.
        await asyncio.sleep(0.5)

    async def start(self) -> None:
        if self._node is not None and self._node_task is not None and not self._node_task.done():
            return

        self._log("importing openhop_core modules")
        SX1262Radio, EventService, EventSubscriber, MeshNode, LocalIdentity = import_openhop_core()
        hardware_config = self.config.hardware or load_hardware_config_from_env()

        self._log(f"radio config {hardware_config.to_log_string()}")

        # Snapshot threads before hardware init so we can track GPIO/IRQ threads
        # spawned by openhop_core and wait for them during stop().
        pre_threads = {t.ident for t in threading.enumerate()}

        self._log("creating SX1262Radio")
        self._radio = create_radio(
            SX1262Radio,
            hardware_config,
            self._log,
        )

        # Retry begin() with backoff — openhop_core calls sys.exit(1) when a
        # GPIO pin is still held by the previous session's edge-detection
        # thread (stuck in gpio.poll(30s)).  Retrying gives the kernel time
        # to release the line after the old fd is closed.
        max_begin_attempts = 4
        for attempt in range(max_begin_attempts):
            self._log(f"calling radio.begin() (attempt {attempt + 1}/{max_begin_attempts})")
            try:
                if not self._radio.begin():
                    raise RuntimeError("radio.begin() returned False – hardware init failed")
                break
            except SystemExit:
                if attempt >= max_begin_attempts - 1:
                    raise RuntimeError("GPIO pins still busy after retries – hardware init failed")
                delay = 2.0 * (attempt + 1)
                self._log(f"GPIO busy, retrying in {delay:.0f}s")
                # Clean up partial radio state before retry
                try:
                    self._radio.cleanup()
                except Exception:
                    pass
                await asyncio.sleep(delay)
                self._radio = create_radio(SX1262Radio, hardware_config, self._log)
        self._log("radio.begin() returned successfully")

        self._event_service = EventService()
        self._log("creating MeshNode")
        self._identity, self._node = create_mesh_node(
            MeshNode,
            LocalIdentity,
            radio=self._radio,
            event_service=self._event_service,
            node_name=self.config.node_name,
            node_config={"share_public_key": self.config.share_public_key},
            channel_db=self._channel_db,
            contacts=self._contact_book,
        )

        self._node.set_event_service(self._event_service)

        self._event_subscriber = attach_event_service_subscriber(
            event_service=self._event_service,
            event_subscriber_base=EventSubscriber,
            emit=self._emit,
            logger=self._log,
        )
        attach_dispatcher_callbacks(node=self._node, emit=self._emit, logger=self._log)

        mode = self.config.path_hash_mode
        if mode and 0 <= mode <= 2:
            self._node.dispatcher.set_default_path_hash_mode(mode)
            self._log(f"path hash mode set to {mode}")
        elif mode:
            self._log(f"ignoring invalid path_hash_mode={mode} (must be 0-2)")

        # Normalization validates, so it stays inside the try: a settings row
        # written by an older build must not stop the radio from connecting.
        try:
            region = normalize_region_scope(self.config.flood_region)
            if region:
                # The persisted default, not the transient send-scope override
                # (dispatcher.flood_transport_key); per-channel scopes and
                # explicit-unscoped sends take precedence over it.
                self._node.dispatcher.default_flood_transport_key = region_transport_key(region)
                self._log(f"flood region scope set to {region}")
        except ValueError as exc:
            self._log(f"ignoring invalid flood region {self.config.flood_region!r}: {exc}")

        self._register_req_handler()
        self._register_discovery_handler()
        # replies to repeater_request(), routed to their waiter by tag
        self._node.dispatcher.protocol_response_handler.set_binary_response_callback(
            self._on_binary_response
        )

        self._log("creating background task for node.start()")
        self._node_task = asyncio.create_task(self._node.start())
        await asyncio.sleep(0.2)
        if self._node_task.done():
            self._log("node.start() task exited early")
            self._node_task.result()
        self._log("node.start() task running")

        # Record threads spawned by hardware init (GPIO edge detection, IRQ handlers).
        post_threads = {t.ident for t in threading.enumerate()}
        self._hw_thread_ids = frozenset(post_threads - pre_threads)

    async def stop(self) -> None:
        if self._event_service is not None and self._event_subscriber is not None:
            try:
                self._event_service.unsubscribe_all(self._event_subscriber)
            except Exception:
                pass

        if self._node is not None:
            self._log("stopping node")
            result = self._node.stop()
            if inspect.isawaitable(result):
                await result

        if self._node_task is not None:
            if not self._node_task.done():
                self._log("cancelling node.start() task")
                self._node_task.cancel()
            try:
                await asyncio.wait_for(self._node_task, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

        # Best-effort radio cleanup for reconnect stability on Linux GPIO/SPI.
        #
        # openhop_core's GPIOPinManager.cleanup_all() joins edge-detection threads
        # (2.0s timeout) BEFORE closing pin file descriptors.  Since those
        # threads block in gpio.poll(30.0), the join always times out.
        # Pre-closing the fds unblocks the threads so cleanup_all()'s join
        # succeeds immediately and the whole stop() fits within the caller's
        # async timeout.
        if self._radio is not None:
            gpio_mgr = getattr(self._radio, "_gpio_manager", None)
            if gpio_mgr is not None:
                for evt in getattr(gpio_mgr, "_edge_stop_events", {}).values():
                    evt.set()
                for pin_obj in list(getattr(gpio_mgr, "_pins", {}).values()):
                    try:
                        pin_obj.close()
                    except Exception:
                        pass

            for method in (
                "stop",
                "shutdown",
                "close",
                "cleanup",
                "deinit",
                "end",
            ):
                await self._call_maybe_async(self._radio, method)
            # Do NOT call lora methods separately.  radio.cleanup() already
            # calls lora.end() then gpio_manager.cleanup_all().  Calling
            # lora.end() again would re-open the busy pin (via busyCheck →
            # _get_input_safe) after cleanup_all() released it, causing
            # "Device or resource busy" on the next start().

        self._radio = None
        self._identity = None
        self._node = None
        self._event_service = None
        self._event_subscriber = None
        self._node_task = None

        # Edge threads should already be dead (fds pre-closed above).
        # Brief settle for the kernel to fully release GPIO lines.
        gc.collect()
        await self._poll_hw_threads(timeout=3.0)

        while not self._event_queue.empty():
            try:
                self._event_queue.get_nowait()
            except queue.Empty:
                break

    def close(self) -> None:
        """Release the session's own database connection.  The client makes a
        new session on every disconnect or failed connect; the old ones kept
        their connection open for as long as the app ran."""
        self._db.close()

    async def send_text(self, peer_name: str, message: str) -> object:
        if self._node is None:
            raise RuntimeError("Session is not started.")
        return await send_text(node=self._node, peer_name=peer_name, message=message)

    async def send_group_text(
        self, channel_name: str, message: str, region_scope: str | None = None
    ) -> object:
        """Broadcast a text message to a group/public channel."""
        if self._node is None:
            raise RuntimeError("Session is not started.")
        return await send_group_text(
            node=self._node,
            channel_name=channel_name,
            message=message,
            region_scope=region_scope,
        )

    async def send_advert(
        self,
        *,
        name: str | None = None,
        lat: float = 0.0,
        lon: float = 0.0,
        route_type: str = "flood",
    ) -> SendResultDict:
        if self._node is None or self._identity is None:
            raise RuntimeError("Session is not started.")
        return await send_advert(
            node=self._node,
            identity=self._identity,
            default_name=self.config.node_name,
            emit=self._emit,
            logger=self._log,
            name=name,
            lat=lat,
            lon=lon,
            route_type=route_type,
        )

    async def discover_nodes(self) -> list[dict[str, Any]]:
        """Zero-hop discover: which repeaters/sensors hear us directly (see discovery.py)."""
        if self._node is None:
            raise RuntimeError("Session is not started.")
        from openhop_core.protocol.packet_builder import PacketBuilder

        dispatcher = self._node.dispatcher

        async def send(packet: Any) -> None:
            await dispatcher.send_packet(packet, wait_for_ack=False)

        results = await discover(
            send,
            dispatcher.control_handler,
            PacketBuilder.create_discovery_request,
            self_pub_key=self.get_public_key(),
        )
        self._log(f"discover: {len(results)} answer(s)")
        return results

    async def request_owner_name(self, pub_key_hex: str) -> str | None:
        """Ask a zero-hop neighbour (e.g. a discovered repeater) for its name.

        openhop_core decrypts the reply through the contact book, so a stand-in
        contact (direct zero-hop route) sits there for the duration of the request.
        """
        if self._node is None:
            raise RuntimeError("Session is not started.")
        contact = Contact(
            name=f"owner-query {pub_key_hex[:8]}",
            public_key=pub_key_hex,
            out_path=[],
            out_path_len=0,
        )
        self._contact_book.contacts.append(contact)
        try:
            reply = await request_owner_info(node=self._node, contact=contact)
        finally:
            self._contact_book.contacts.remove(contact)
        decoded = decode_owner_reply(reply) if reply else None
        self._log(f"owner query {pub_key_hex[:8]}: {decoded[0] if decoded else 'no answer'}")
        return decoded[0] if decoded else None

    async def send_telemetry_request(
        self,
        contact_name: str,
        *,
        want_location: bool = True,
        timeout: float = 10.0,
    ) -> dict:
        """Request telemetry from a remote peer. Returns decoded sensor data."""
        if self._node is None:
            raise RuntimeError("Session is not started.")
        return await request_telemetry(
            node=self._node,
            contact_name=contact_name,
            want_location=want_location,
            timeout=timeout,
        )

    async def send_repeater_login(self, peer_name: str, password: str) -> dict:
        if self._node is None:
            raise RuntimeError("Session is not started.")
        return await send_login(node=self._node, peer_name=peer_name, password=password)

    async def send_repeater_logout(self, peer_name: str) -> dict:
        if self._node is None:
            raise RuntimeError("Session is not started.")
        return await send_logout(node=self._node, peer_name=peer_name)

    async def send_repeater_cli(self, peer_name: str, command: str, timeout: float = 15.0) -> dict:
        if self._node is None:
            raise RuntimeError("Session is not started.")
        return await send_repeater_command(
            node=self._node,
            peer_name=peer_name,
            command=command,
            timeout=timeout,
            timestamp=self.next_request_timestamp(),
        )

    async def repeater_request(
        self, peer_name: str, req_type: int, data: bytes = b"", timeout: float = 10.0
    ) -> bytes | None:
        """A binary request to a logged-in repeater; the raw reply, or None."""
        if self._node is None:
            raise RuntimeError("Session is not started.")
        return await request_repeater_data(
            node=self._node,
            peer_name=peer_name,
            req_type=req_type,
            data=data,
            timestamp=self.next_request_timestamp(),
            waiters=self._raw_waiters,
            timeout=timeout,
        )

    def next_request_timestamp(self) -> int:
        """Strictly increasing request timestamps.  A repeater drops a request
        whose timestamp is not newer than the client's previous one (replay
        protection), so two requests within the same second lost the second."""
        self._last_request_ts = max(int(time.time()), self._last_request_ts + 1)
        return self._last_request_ts

    def _on_binary_response(self, tag: bytes, payload: bytes, _path_info: object) -> None:
        waiter = self._raw_waiters.pop(bytes(tag), None)
        if waiter is not None:
            waiter(bytes(payload))

    async def listen_events(self) -> AsyncIterator[MeshEventDict]:
        if self._node is None:
            raise RuntimeError("Session is not started.")
        while True:
            yield await asyncio.to_thread(self._event_queue.get)

    def drain_events(self, max_items: int = 100) -> list[MeshEventDict]:
        items: list[MeshEventDict] = []
        for _ in range(max_items):
            try:
                items.append(self._event_queue.get_nowait())
            except queue.Empty:
                break
        return items

    def status(self) -> SessionStatusDict:
        return {
            "connected": self._node is not None,
            "node_name": self.config.node_name,
            "board": "hackergadgets-aio",
            "openhop_core_version": self._get_openhop_version(),
        }

    @staticmethod
    def _get_openhop_version() -> str:
        try:
            import openhop_core

            return openhop_core.__version__
        except Exception:
            return "unknown"

    @property
    def contact_book(self) -> ContactBook:
        return self._contact_book

    def get_public_key(self) -> str | None:
        """Return this node's public key as a hex string, or None if unavailable."""
        if self._identity is None:
            return None
        # The Ed25519 identity key: what adverts, path hashes and contacts use.
        # get_shared_public_key() is the X25519 ECDH key, nobody's address.
        return self._identity.get_public_key().hex()
