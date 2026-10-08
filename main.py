import threading

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.lang import Builder
from kivy.utils import escape_markup, platform

from client_core import ChatClient
from server_core import ChatServer, get_local_ip

KV = """
#:import dp kivy.metrics.dp

<Btn@Button>:
    size_hint_y: None
    height: dp(48)

<Input@TextInput>:
    size_hint_y: None
    height: dp(44)
    multiline: False
    write_tab: False

ScreenManager:
    Screen:
        name: 'connect'
        BoxLayout:
            orientation: 'vertical'
            padding: dp(20)
            spacing: dp(10)
            Label:
                text: 'Мессенджер'
                font_size: '30sp'
                size_hint_y: None
                height: dp(70)
            Input:
                id: nick
                hint_text: 'Ваше имя'
            Input:
                id: host
                hint_text: 'IP сервера'
                text: '127.0.0.1'
            Input:
                id: port
                hint_text: 'Порт'
                text: '5555'
                input_filter: 'int'
            Btn:
                text: 'Подключиться'
                on_release: app.do_connect(nick.text, host.text, port.text)
            Btn:
                text: 'Запустить сервер на этом устройстве и войти'
                on_release: app.start_host(nick.text, port.text)
            Label:
                id: status
                color: 1, .4, .4, 1
                text_size: self.width, None
                halign: 'center'
                size_hint_y: None
                height: self.texture_size[1] + dp(10)
            Widget:

    Screen:
        name: 'chat'
        BoxLayout:
            orientation: 'vertical'
            BoxLayout:
                size_hint_y: None
                height: dp(48)
                padding: dp(8), 0
                spacing: dp(8)
                Label:
                    id: info
                    text: ''
                    halign: 'left'
                    valign: 'middle'
                    text_size: self.size
                    shorten: True
                Button:
                    text: 'Выйти'
                    size_hint_x: None
                    width: dp(90)
                    on_release: app.leave()
            ScrollView:
                id: scroll
                do_scroll_x: False
                bar_width: dp(4)
                Label:
                    id: log
                    markup: True
                    text: ''
                    size_hint_y: None
                    height: self.texture_size[1] + dp(16)
                    text_size: self.width - dp(16), None
                    halign: 'left'
                    valign: 'top'
                    padding: dp(8), dp(8)
            BoxLayout:
                size_hint_y: None
                height: dp(52)
                padding: dp(6)
                spacing: dp(6)
                TextInput:
                    id: msg
                    hint_text: 'Сообщение...'
                    multiline: False
                    write_tab: False
                    on_text_validate: app.send(self)
                Button:
                    text: 'Отпр.'
                    size_hint_x: None
                    width: dp(80)
                    on_release: app.send(msg)
"""


class MessengerApp(App):
    title = "Мессенджер"

    def build(self):
        self.client = None
        self.server = None
        self.lines = []
        self.my_nick = ""
        self.users = []
        if platform not in ("android", "ios"):
            Window.size = (420, 720)
        return Builder.load_string(KV)

    # ---------- подключение ----------
    def set_status(self, text):
        self.root.get_screen("connect").ids.status.text = text

    def start_host(self, nick, port):
        try:
            port = int(port or 5555)
            self.server = ChatServer(port=port)
            self.server.start()
        except OSError as e:
            self.server = None
            self.set_status(f"Не удалось запустить сервер: {e}")
            return
        self.host_ip = get_local_ip()
        self.do_connect(nick, "127.0.0.1", str(port))

    def do_connect(self, nick, host, port):
        nick = nick.strip()
        if not nick:
            self.set_status("Введите имя")
            return
        self.set_status("Подключение...")
        threading.Thread(target=self._connect_thread,
                         args=(nick, host.strip(), port), daemon=True).start()

    def _connect_thread(self, nick, host, port):
        client = ChatClient(self.on_event)
        try:
            client.connect(host, int(port), nick)
        except (OSError, ValueError) as e:
            Clock.schedule_once(lambda dt: self._connect_failed(e))
            return
        self.client = client

    def _connect_failed(self, err):
        self.set_status(f"Ошибка подключения: {err}")
        if self.server:
            self.server.stop()
            self.server = None

    # ---------- события от сервера (из фонового потока) ----------
    def on_event(self, ev):
        Clock.schedule_once(lambda dt: self.handle(ev))

    def handle(self, ev):
        t = ev.get("type")
        if t == "welcome":
            self.my_nick = ev["nick"]
            self.lines = []
            self.root.current = "chat"
            self.refresh_info()
        elif t == "history":
            for m in ev["messages"]:
                self.add_message(m)
        elif t == "msg":
            self.add_message(ev)
        elif t == "system":
            self.add_line(f"[i][color=888888]{escape_markup(ev['text'])}[/color][/i]")
        elif t == "users":
            self.users = ev["users"]
            self.refresh_info()
        elif t == "disconnected":
            if self.root.current == "chat":
                self.add_line("[color=ff6666]Соединение потеряно[/color]")

    def refresh_info(self):
        ids = self.root.get_screen("chat").ids
        text = f"{self.my_nick} | онлайн: {len(self.users)}"
        if self.server:
            text += f" | сервер: {self.host_ip}:{self.server.port}"
        ids.info.text = text

    # ---------- чат ----------
    def add_message(self, m):
        mine = m["nick"] == self.my_nick
        color = "66ccff" if mine else "ffcc66"
        self.add_line(
            f"[color=888888]{m['ts']}[/color] "
            f"[b][color={color}]{escape_markup(m['nick'])}[/color][/b]: "
            f"{escape_markup(m['text'])}"
        )

    def add_line(self, markup):
        self.lines.append(markup)
        ids = self.root.get_screen("chat").ids
        ids.log.text = "\n".join(self.lines)
        Clock.schedule_once(lambda dt: setattr(ids.scroll, "scroll_y", 0), 0.05)

    def send(self, field):
        text = field.text.strip()
        if text and self.client:
            if not self.client.send_text(text):
                self.add_line("[color=ff6666]Не удалось отправить[/color]")
        field.text = ""
        Clock.schedule_once(lambda dt: setattr(field, "focus", True), 0.1)

    def leave(self):
        if self.client:
            self.client.close()
            self.client = None
        if self.server:
            self.server.stop()
            self.server = None
        self.users = []
        self.root.current = "connect"
        self.set_status("")

    def on_stop(self):
        self.leave()


if __name__ == "__main__":
    MessengerApp().run()
