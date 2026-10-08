"""Сервер мессенджера: TCP, JSON-сообщения по одному на строку.

Можно использовать как модуль (из приложения) или запустить отдельно:
    python server_core.py [порт]
"""
import json
import socket
import sys
import threading
import time

HISTORY_LIMIT = 100


def get_local_ip():
    """Локальный IP устройства в Wi-Fi/LAN (пакеты при этом не отправляются)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class ChatServer:
    def __init__(self, host="0.0.0.0", port=5555):
        self.host = host
        self.port = port
        self.sock = None
        self.running = False
        self.clients = {}  # socket -> ник
        self.history = []
        self.lock = threading.Lock()

    # ---------- управление ----------
    def start(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.host, self.port))
        self.sock.listen(20)
        self.running = True
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def stop(self):
        self.running = False
        try:
            self.sock.close()
        except OSError:
            pass
        with self.lock:
            for c in list(self.clients):
                try:
                    c.close()
                except OSError:
                    pass
            self.clients.clear()

    # ---------- внутренности ----------
    def _accept_loop(self):
        while self.running:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                break
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    @staticmethod
    def _send(conn, obj):
        data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        conn.sendall(data)

    def _broadcast(self, obj):
        with self.lock:
            targets = list(self.clients)
        for c in targets:
            try:
                self._send(c, obj)
            except OSError:
                pass

    def _users(self):
        with self.lock:
            return sorted(self.clients.values())

    def _unique_nick(self, nick):
        nick = (nick or "Гость").strip()[:24] or "Гость"
        with self.lock:
            taken = set(self.clients.values())
        base, i = nick, 2
        while nick in taken:
            nick = f"{base}{i}"
            i += 1
        return nick

    def _handle(self, conn):
        nick = None
        try:
            f = conn.makefile("r", encoding="utf-8", newline="\n")
            first = json.loads(f.readline())
            if first.get("type") != "join":
                return
            nick = self._unique_nick(first.get("nick"))
            self._send(conn, {"type": "welcome", "nick": nick})
            self._send(conn, {"type": "history", "messages": list(self.history)})
            with self.lock:
                self.clients[conn] = nick
            self._broadcast({"type": "system", "text": f"{nick} вошёл в чат"})
            self._broadcast({"type": "users", "users": self._users()})

            for line in f:
                try:
                    m = json.loads(line)
                except ValueError:
                    continue
                if m.get("type") == "msg":
                    text = str(m.get("text", "")).strip()[:2000]
                    if not text:
                        continue
                    msg = {"type": "msg", "nick": nick, "text": text,
                           "ts": time.strftime("%H:%M")}
                    self.history.append(msg)
                    del self.history[:-HISTORY_LIMIT]
                    self._broadcast(msg)
        except (OSError, ValueError):
            pass
        finally:
            with self.lock:
                was_in = self.clients.pop(conn, None)
            try:
                conn.close()
            except OSError:
                pass
            if was_in:
                self._broadcast({"type": "system", "text": f"{was_in} вышел из чата"})
                self._broadcast({"type": "users", "users": self._users()})


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5555
    srv = ChatServer(port=port)
    srv.start()
    print(f"Сервер запущен: {get_local_ip()}:{port}  (Ctrl+C для остановки)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        srv.stop()
