import threading
import io
import time
import sys
import pytest
from connpy import printer

def test_printer_thread_isolation():
    """Verify that printer output is isolated per thread when using set_thread_stream."""
    num_threads = 5
    iterations = 20
    results = {}
    
    def worker(thread_id):
        # Create a private buffer for this thread
        buf = io.StringIO()
        printer.set_thread_stream(buf)
        
        # Ensure we have a clean console for this thread
        # In a real gRPC request, this happens automatically as it's a new thread
        printer.set_thread_console(None) 
        
        # Each thread prints its own ID
        expected_msg = f"Thread-{thread_id}"
        for _ in range(iterations):
            printer.info(expected_msg)
            time.sleep(0.01)
            
        results[thread_id] = buf.getvalue()
        printer.set_thread_stream(None)

    threads = []
    for i in range(num_threads):
        t = threading.Thread(target=worker, args=(i,))
        threads.append(t)
        t.start()
        
    for t in threads:
        t.join()
        
    # Validation
    for thread_id, output in results.items():
        expected_msg = f"Thread-{thread_id}"
        assert expected_msg in output
        
        # Ensure no leaks
        for other_id in range(num_threads):
            if other_id == thread_id: continue
            assert f"Thread-{other_id}" not in output

def test_printer_manual_stream():
    """Verify that setting a thread stream correctly captures printer output in the current thread."""
    buf = io.StringIO()
    
    # We must clear the thread-local console to force it to pick up the new sys.stdout proxy
    printer.set_thread_console(None)
    printer.set_thread_stream(buf)
    
    printer.info("Captured-Message")
    
    output = buf.getvalue()
    printer.set_thread_stream(None)
    printer.set_thread_console(None)
    
    assert "Captured-Message" in output
