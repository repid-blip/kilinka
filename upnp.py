"""Автоматический проброс порта на роутере через UPnP (только стандартная библиотека).

Нужен, чтобы устройства из разных сетей могли подключаться к серверу,
запущенному на этом устройстве. Работает, если роутер поддерживает UPnP
и у провайдера есть «белый» IP (не CGNAT).
"""
import ipaddress
import re
import socket
import urllib.request
from urllib.parse import urljoin, urlparse

SSDP_ADDR = ("239.255.255.250", 1900)
SSDP_ST = "urn:schemas-upnp-org:device:InternetGatewayDevice:1"
WAN_TYPES = ("WANIPConnection", "WANPPPConnection")
CGNAT_NET = ipaddress.ip_network("100.64.0.0/10")


def _discover(timeout=2.5):
    msg = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n"
           'MAN: "ssdp:discover"\r\nMX: 2\r\nST: %s\r\n\r\n' % SSDP_ST).encode()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    found = []
    try:
        s.sendto(msg, SSDP_ADDR)
        while True:
            try:
                data, _ = s.recvfrom(65507)
            except OSError:  # таймаут — ответы закончились
                break
            m = re.search(r"(?im)^location:\s*(\S+)", data.decode("utf-8", "replace"))
            if m and m.group(1) not in found:
                found.append(m.group(1))
    finally:
        s.close()
    return found


def _find_control(location):
    """Возвращает (serviceType, controlURL) службы WANIPConnection/WANPPPConnection."""
    with urllib.request.urlopen(location, timeout=4) as r:
        xml = r.read().decode("utf-8", "replace")
    for block in re.findall(r"<service>(.*?)</service>", xml, re.S):
        st = re.search(r"<serviceType>\s*(.*?)\s*</serviceType>", block, re.S)
        cu = re.search(r"<controlURL>\s*(.*?)\s*</controlURL>", block, re.S)
        if st and cu and any(w in st.group(1) for w in WAN_TYPES):
            return st.group(1), urljoin(location, cu.group(1))
    return None


def _soap(url, stype, action, args=()):
    params = "".join("<%s>%s</%s>" % (k, v, k) for k, v in args)
    body = (
        '<?xml version="1.0"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        '<s:Body><u:%s xmlns:u="%s">%s</u:%s></s:Body></s:Envelope>'
        % (action, stype, params, action)
    ).encode()
    req = urllib.request.Request(url, body, {
        "Content-Type": 'text/xml; charset="utf-8"',
        "SOAPAction": '"%s#%s"' % (stype, action),
    })
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.read().decode("utf-8", "replace")


def _local_ip_towards(host):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((host, 9))
        return s.getsockname()[0]
    finally:
        s.close()


def get_public_ip():
    """Внешний IP, каким нас видит интернет (или '' если узнать не удалось)."""
    for url in ("http://api.ipify.org", "http://ifconfig.me/ip", "http://icanhazip.com"):
        try:
            with urllib.request.urlopen(url, timeout=4) as r:
                ip = r.read().decode().strip()
            ipaddress.ip_address(ip)
            return ip
        except (OSError, ValueError):
            continue
    return ""


def _is_shared_or_private(ip):
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return a.is_private or a in CGNAT_NET


class PortMapper:
    def __init__(self):
        self._ctl = None
        self.port = None

    def open(self, port):
        """Пробрасывает TCP-порт. Возвращает dict:
        ok, router_ip, public_ip, cgnat, error."""
        res = {"ok": False, "router_ip": "", "public_ip": "", "cgnat": False, "error": ""}
        try:
            ctl = None
            for loc in _discover():
                try:
                    ctl = _find_control(loc)
                except OSError:
                    continue
                if ctl:
                    gw = urlparse(loc).hostname
                    break
            if not ctl:
                res["error"] = "роутер не отвечает по UPnP (выключен или не поддерживается)"
            else:
                stype, url = ctl
                local = _local_ip_towards(gw)
                err = None
                for lease in ("0", "7200"):  # часть роутеров не принимает бессрочную аренду
                    try:
                        _soap(url, stype, "AddPortMapping", [
                            ("NewRemoteHost", ""), ("NewExternalPort", port),
                            ("NewProtocol", "TCP"), ("NewInternalPort", port),
                            ("NewInternalClient", local), ("NewEnabled", 1),
                            ("NewPortMappingDescription", "Messenger"),
                            ("NewLeaseDuration", lease)])
                        err = None
                        break
                    except OSError as e:
                        err = e
                if err:
                    res["error"] = "роутер отклонил проброс порта: %s" % err
                else:
                    self._ctl, self.port = ctl, port
                    res["ok"] = True
                    try:
                        xml = _soap(url, stype, "GetExternalIPAddress")
                        m = re.search(r"<NewExternalIPAddress>(.*?)</NewExternalIPAddress>", xml)
                        res["router_ip"] = m.group(1).strip() if m else ""
                    except OSError:
                        pass
        except OSError as e:
            res["error"] = str(e) or e.__class__.__name__
        res["public_ip"] = get_public_ip()
        r_ip, p_ip = res["router_ip"], res["public_ip"]
        if r_ip and r_ip != "0.0.0.0":
            res["cgnat"] = _is_shared_or_private(r_ip) or (bool(p_ip) and r_ip != p_ip)
        return res

    def close(self):
        if self._ctl and self.port:
            stype, url = self._ctl
            try:
                _soap(url, stype, "DeletePortMapping", [
                    ("NewRemoteHost", ""), ("NewExternalPort", self.port),
                    ("NewProtocol", "TCP")])
            except OSError:
                pass
        self._ctl = self.port = None
