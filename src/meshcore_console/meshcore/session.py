from __future__ import annotations

import asyncio
import gc
import inspect
import logging
import queue
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

# pymc_core's ProtocolResponseHandler tries ``from utils.cayenne_lpp_helpers
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
from .contact_book import ContactBook
from .db import open_db
from .event_bridge import attach_dispatcher_callbacks, attach_event_service_subscriber
from .operations import request_telemetry, send_advert, send_group_text, send_text
from .runtime import create_mesh_node, create_radio, import_pymc_core


class PyMCCoreSession:
    """Async wrapper around pymc_core MeshNode lifecycle."""

    def __init__(self, config: RuntimeRadioConfig, logger: LoggerCallback | None = None) -> None:
        self.config = config
        self._logger = logger
        self._radio: SX1262RadioProtocol | None = None
        self._identity: LocalIdentityProtocol | None = None
        self._node: MeshNodeProtocol | None = None
        self._event_service: EventServiceProtocol | None = None
        self._event_subscriber: EventSubscriberProtocol | None = None
        self._node_task: asyncio.Task[None] | None = None
        self._event_queue: queue.Queue[MeshEventDict] = queue.Queue()
        self._event_notify: Callable[[], None] | None = None
        self._telemetry_data_fn: Callable[[], dict[str, Any]] | None = None
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

    def _emit(self, payload: MeshEventDict) -> None:
        self._event_queue.put_nowait(payload)
        if self._event_notify is not None:
            try:
                self._event_notify()
            except Exception:  # noqa: BLE001
                pass  # Must never crash the radio driver

    def _register_discovery_handler(self) -> None:
        """Register a handler that responds to incoming discovery requests.

        When another node sends a CONTROL discovery request, we reply with
        our public key and node type so they can discover us.
        """
        from pymc_core.protocol.constants import ADVERT_FLAG_IS_CHAT_NODE
        from pymc_core.protocol.packet_builder import PacketBuilder

        assert self._node is not None
        assert self._identity is not None

        dispatcher = self._node.dispatcher
        control_handler = dispatcher.control_handler

        # Capture identity ref for the closure
        identity = self._identity

        def _on_discovery_request(request_data: dict) -> None:
            tag = request_data.get("tag", 0)
            prefix_only = request_data.get("prefix_only", False)
            inbound_snr = request_data.get("snr", 0.0)

            # Get our public key
            get_pk = getattr(identity, "get_shared_public_key", None)
            pub_key = get_pk() if callable(get_pk) else None
            if pub_key is None:
                return
            if isinstance(pub_key, str):
                pub_key = bytes.fromhex(pub_key)

            response = PacketBuilder.create_discovery_response(
                tag=tag,
                node_type=ADVERT_FLAG_IS_CHAT_NODE,
                inbound_snr=inbound_snr,
                pub_key=pub_key,
                prefix_only=prefix_only,
            )

            async def _send() -> None:
                try:
                    await dispatcher.send_packet(response, wait_for_ack=False)
                    self._log(f"sent discovery response for tag=0x{tag:08X}")
                except Exception as exc:
                    self._log(f"failed to send discovery response: {exc}")

            try:
                loop = asyncio.get_running_loop()
                loop.create_task(_send())
            except RuntimeError:
                pass  # No running loop — skip

        control_handler.set_request_callback(_on_discovery_request)

    def _build_telemetry_handler(self) -> Callable[..., bytes | None]:
        """Build a handler closure for inbound telemetry requests (REQ type 0x03)."""
        TELEM_PERM_LOCATION = 0x02  # noqa: N806

        def _handle_telemetry(_client: Any, _timestamp: int, req_data: bytes) -> bytes | None:
            if self._telemetry_data_fn is None:
                return None
            data = self._telemetry_data_fn()
            if not data.get("allow"):
                self._log("telemetry request denied (allow_telemetry=False)")
                return None
            mask = req_data[0] if req_data else 0x00
            include_location = not (mask & TELEM_PERM_LOCATION)
            lat = data.get("lat") if include_location else None
            lon = data.get("lon") if include_location else None
            voltage = data.get("voltage")
            payload = encode_telemetry(
                channel=1,
                lat=lat,
                lon=lon,
                voltage=voltage,
            )
            if not payload:
                self._log("telemetry request: no sensor data available")
                return None
            self._log(f"responding to telemetry request (voltage={voltage}, lat={lat}, lon={lon})")
            return payload

        return _handle_telemetry

    def _register_req_handler(self) -> None:
        """Register handlers for incoming REQ and ANON_REQ packets.

        pyMC_core's register_default_handlers() does not register a handler
        for PAYLOAD_TYPE_REQ (0x00), so incoming requests are silently
        dropped.  We register ProtocolRequestHandler here and wrap it so
        that any generated RESPONSE packet is actually transmitted.

        ANON_REQ (0x07) packets carry the sender's public key inline, so
        they don't require prior key exchange.  The default AnonReqResponseHandler
        only handles login responses; we replace it with one that also handles
        telemetry requests.
        """
        import struct

        from pymc_core.node.handlers.protocol_request import ProtocolRequestHandler
        from pymc_core.protocol import CryptoUtils, Identity, PacketBuilder
        from pymc_core.protocol.constants import (
            PAYLOAD_TYPE_ANON_REQ,
            PAYLOAD_TYPE_REQ,
            PAYLOAD_TYPE_RESPONSE,
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
                peer_id = Identity(bytes.fromhex(contact.public_key))
                ss = peer_id.calc_shared_secret(our_privkey)
                aes_key = ss[:16]
                try:
                    plaintext = CryptoUtils.mac_then_decrypt(
                        aes_key,
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

        dispatcher.register_handler(PAYLOAD_TYPE_REQ, _handle_req)

        # --- ANON_REQ handler ---
        existing_anon = dispatcher._handler_instances.get(PAYLOAD_TYPE_ANON_REQ)

        async def _handle_anon_req(pkt: Any) -> None:
            if len(pkt.payload) < 34:
                return

            # Login response check: sender included OUR pubkey → delegate
            if pkt.payload[1:33] == our_pubkey and existing_anon is not None:
                await existing_anon(pkt)
                return

            dest_hash = pkt.payload[0]
            if dest_hash != our_hash:
                return

            client_pubkey = bytes(pkt.payload[1:33])
            encrypted_data = bytes(pkt.payload[33:])
            client_identity = Identity(client_pubkey)
            shared_secret = client_identity.calc_shared_secret(our_privkey)
            aes_key = shared_secret[:16]

            try:
                plaintext = CryptoUtils.mac_then_decrypt(
                    aes_key,
                    shared_secret,
                    encrypted_data,
                )
            except Exception as e:
                self._log(f"ANON_REQ decrypt failed: {e}")
                return

            if len(plaintext) < 5:
                self._log("ANON_REQ payload too short")
                return

            timestamp = struct.unpack("<I", plaintext[0:4])[0]
            req_type = plaintext[4]
            req_data = plaintext[5:] if len(plaintext) > 5 else b""
            contact = contact_book.get_by_key(client_pubkey)
            sender = contact.name if contact else f"{client_pubkey[:4].hex()}..."
            self._log(f"ANON_REQ from {sender} type=0x{req_type:02X} ts={timestamp}")

            handler_fn = request_handlers.get(req_type)
            if handler_fn is None:
                self._log(f"ANON_REQ: no handler for type 0x{req_type:02X}")
                return

            response_payload = handler_fn(contact, timestamp, req_data)
            if response_payload is None:
                self._log("ANON_REQ: handler returned no data")
                return

            reply_data = struct.pack("<I", timestamp) + response_payload
            client_hash = client_pubkey[0]
            path_list = list(pkt.path[: pkt.path_len]) if pkt.path_len > 0 else []

            try:
                response_pkt = PacketBuilder.create_path_return(
                    dest_hash=client_hash,
                    src_hash=our_hash,
                    secret=shared_secret,
                    path=path_list,
                    extra_type=PAYLOAD_TYPE_RESPONSE,
                    extra=bytes(reply_data),
                )
                self._log(
                    f"ANON_REQ: sending PATH response to "
                    f"0x{client_hash:02X} ({len(response_pkt.payload)} bytes)"
                )
                await dispatcher.send_packet(response_pkt, wait_for_ack=False)
            except Exception as e:
                self._log(f"ANON_REQ: failed to build/send response: {e}")

        dispatcher.register_handler(PAYLOAD_TYPE_ANON_REQ, _handle_anon_req)

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

        pyMC_core's GPIOPinManager creates OS threads for edge detection and
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

        self._log("importing pymc_core modules")
        SX1262Radio, EventService, EventSubscriber, MeshNode, LocalIdentity = import_pymc_core()
        hardware_config = self.config.hardware or load_hardware_config_from_env()

        self._log(f"radio config {hardware_config.to_log_string()}")

        # Snapshot threads before hardware init so we can track GPIO/IRQ threads
        # spawned by pyMC_core and wait for them during stop().
        pre_threads = {t.ident for t in threading.enumerate()}

        self._log("creating SX1262Radio")
        self._radio = create_radio(
            SX1262Radio,
            hardware_config,
            self._log,
        )

        # Retry begin() with backoff — pymc_core calls sys.exit(1) when a
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

        self._register_req_handler()
        self._register_discovery_handler()

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
        # pymc_core's GPIOPinManager.cleanup_all() joins edge-detection threads
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

    async def send_text(self, peer_name: str, message: str) -> object:
        if self._node is None:
            raise RuntimeError("Session is not started.")
        return await send_text(node=self._node, peer_name=peer_name, message=message)

    async def send_group_text(self, channel_name: str, message: str) -> object:
        """Broadcast a text message to a group/public channel."""
        if self._node is None:
            raise RuntimeError("Session is not started.")
        return await send_group_text(node=self._node, channel_name=channel_name, message=message)

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
            "pymc_core_version": self._get_pymc_version(),
        }

    @staticmethod
    def _get_pymc_version() -> str:
        try:
            import pymc_core

            return pymc_core.__version__
        except Exception:
            return "unknown"

    @property
    def contact_book(self) -> ContactBook:
        return self._contact_book

    def get_public_key(self) -> str | None:
        """Return this node's public key as a hex string, or None if unavailable."""
        if self._identity is None:
            return None
        # pyMC_core LocalIdentity exposes get_shared_public_key()
        get_pk = getattr(self._identity, "get_shared_public_key", None)
        pk = get_pk() if callable(get_pk) else None
        if pk is None:
            return None
        if isinstance(pk, bytes):
            return pk.hex()
        if isinstance(pk, str):
            return pk
        return None
