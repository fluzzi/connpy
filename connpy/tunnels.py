import asyncio
import os
import sys
import termios
import tty
import signal
import struct
import fcntl

class LocalStream:
    """
    Asynchronous stream wrapper for local stdin/stdout.
    Handles terminal raw mode, async I/O, and SIGWINCH signals.
    """
    def __init__(self):
        self.stdin_fd = sys.stdin.fileno()
        self.stdout_fd = sys.stdout.fileno()
        self.original_tty_settings = None
        self.resize_callback = None
        self._reader_queue = asyncio.Queue()
        self._loop = None

    def setup(self, resize_callback=None):
        self._loop = asyncio.get_running_loop()
        self.resize_callback = resize_callback
        
        # Save original terminal settings
        try:
            self.original_tty_settings = termios.tcgetattr(self.stdin_fd)
            tty.setraw(self.stdin_fd)
        except termios.error:
            # Not a TTY, maybe piped or redirected
            pass

        # Set stdin non-blocking
        flags = fcntl.fcntl(self.stdin_fd, fcntl.F_GETFL)
        fcntl.fcntl(self.stdin_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        # Setup read callback
        self._loop.add_reader(self.stdin_fd, self._read_ready)

        # Register SIGWINCH
        if resize_callback:
            try:
                self._loop.add_signal_handler(signal.SIGWINCH, self._handle_winch)
            except (NotImplementedError, RuntimeError):
                # signal handling not supported on some loops (e.g., Windows Proactor)
                pass

    def teardown(self):
        if self._loop:
            try:
                self._loop.remove_reader(self.stdin_fd)
            except Exception:
                pass
            if self.resize_callback:
                try:
                    self._loop.remove_signal_handler(signal.SIGWINCH)
                except Exception:
                    pass

        # Restore terminal settings
        if self.original_tty_settings is not None:
            try:
                termios.tcsetattr(self.stdin_fd, termios.TCSADRAIN, self.original_tty_settings)
            except termios.error:
                pass
                
        # Restore blocking mode for stdin
        try:
            flags = fcntl.fcntl(self.stdin_fd, fcntl.F_GETFL)
            fcntl.fcntl(self.stdin_fd, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)
        except Exception:
            pass

    def _read_ready(self):
        try:
            # Read whatever is available
            data = os.read(self.stdin_fd, 4096)
            if data:
                self._reader_queue.put_nowait(data)
            else:
                self._reader_queue.put_nowait(b'') # EOF
        except BlockingIOError:
            pass
        except OSError:
             self._reader_queue.put_nowait(b'') # EOF on error

    async def read(self) -> bytes:
        """Asynchronously read bytes from stdin."""
        return await self._reader_queue.get()

    async def write(self, data: bytes):
        """Asynchronously write bytes to stdout."""
        if not data:
            return
        
        try:
            os.write(self.stdout_fd, data)
        except OSError:
            pass

    def _handle_winch(self):
        if self.resize_callback:
            try:
                # Use ioctl to get the current window size
                s = struct.pack("HHHH", 0, 0, 0, 0)
                a = fcntl.ioctl(self.stdout_fd, termios.TIOCGWINSZ, s)
                rows, cols, _, _ = struct.unpack("HHHH", a)
                
                # We schedule the callback safely inside the asyncio loop
                # instead of running it raw in the signal handler
                self._loop.call_soon(self.resize_callback, rows, cols)
            except Exception:
                pass


import threading

class RemoteStream:
    """
    Asynchronous stream wrapper for gRPC remote connections.
    Bridges the blocking gRPC iterators with the async _async_interact_loop.
    """
    def __init__(self, request_iterator, response_queue):
        self.request_iterator = request_iterator
        self.response_queue = response_queue
        self.running = True
        self._reader_queue = asyncio.Queue()
        self.resize_callback = None
        self._loop = None
        self.t = None

    def setup(self, resize_callback=None):
        self._loop = asyncio.get_running_loop()
        self.resize_callback = resize_callback
        
        def read_requests():
            try:
                for req in self.request_iterator:
                    if not self.running:
                        break
                    if req.cols > 0 and req.rows > 0:
                        if self.resize_callback:
                            self._loop.call_soon_threadsafe(self.resize_callback, req.rows, req.cols)
                    if req.stdin_data:
                        self._loop.call_soon_threadsafe(self._reader_queue.put_nowait, req.stdin_data)
            except Exception:
                pass
            finally:
                if self._loop and not self._loop.is_closed():
                    try:
                        self._loop.call_soon_threadsafe(self._reader_queue.put_nowait, b'')
                    except RuntimeError:
                        pass
                
        self.t = threading.Thread(target=read_requests, daemon=True)
        self.t.start()

    def teardown(self):
        self.running = False
        self.response_queue.put(None) # Signal EOF

    async def read(self) -> bytes:
        """Asynchronously read bytes from the gRPC iterator queue."""
        return await self._reader_queue.get()

    async def write(self, data: bytes):
        """Asynchronously write bytes to the gRPC response queue."""
        if data:
            self.response_queue.put(data)
