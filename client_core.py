"""Сетевая часть клиента (без привязки к GUI)."""
import json
import socket
import threading


class ChatClient:
    def __init__(self, on_event):
        """on_event(dict) вызывается из фонового потока."""
        self.on_event = on_event
        self.sock = None
        self.alive = False

    def connect(self, host, port, nick):
        self.sock = socket.create_connection((host, port), timeout=5)
        self.sock.settimeout(None)
        self.alive = True
        self._send({"type": "join", "nick": nick})
        threading.Thread(target=self._reader, daemon=True).start()

    def _send(self, obj):
        data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        self.sock.sendall(data)

    def send_text(self, text):
        try:
            self._send({"type": "msg", "text": text})
            return True
        except (OSError, AttributeError):
            return False

    def _reader(self):
        try:
            f = self.sock.makefile("r", encoding="utf-8", newline="\n")
            for line in f:
                try:
                    self.on_event(json.loads(line))
                except ValueError:
                    continue
        except OSError:
            pass
        finally:
            if self.alive:
                self.alive = False
                self.on_event({"type": "disconnected"})

    def close(self):
        self.alive = False
        try:
            self.sock.close()
        except (OSError, AttributeError):
            pass
