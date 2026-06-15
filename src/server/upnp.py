"""UPnP IGD 자동 포트개방 (stdlib only).

서버가 시작 시 라우터(IGD)에 포트포워딩을 스스로 요청해서, 사용자가 공유기
설정을 만지거나 cloudflared 같은 외부 터널을 쓰지 않아도 외부에서 접속되게 한다.
("스팀 데디서버처럼 그냥 실행" — 라우터가 UPnP를 허용하는 경우)

사용:
    mgr = UpnpManager(port=9700)
    if mgr.discover():
        mgr.add_mapping()          # 포트 개방
        ip = mgr.get_public_ip()   # 외부에서 보이는 공인 IP
        ...
        mgr.delete_mapping()       # 종료 시 정리

라우터가 UPnP 미지원/거부면 discover()/add_mapping() 이 False 를 반환한다.
"""
from __future__ import annotations

import re
import socket
import urllib.request
from typing import Optional
from xml.etree import ElementTree as ET

from v.logger import get_logger

logger = get_logger("qonvo.upnp")

_DEV_NS = "{urn:schemas-upnp-org:device-1-0}"
# 라우터가 보고하는 외부 IP 가 사설이면(이중 NAT) 이 echo 서비스로 진짜 공인 IP 확인
_PUBLIC_IP_ECHOS = ("https://api.ipify.org", "https://ifconfig.me/ip", "https://icanhazip.com")


class UpnpManager:
    """단일 TCP 포트에 대한 UPnP 매핑 수명주기 관리."""

    def __init__(self, port: int, description: str = "Qonvo Server", lease: int = 3600):
        self.port = port
        self.description = description
        self.lease = lease  # 초. 0=영구(라우터가 허용 시). 그 외엔 주기적 갱신 필요
        self._svctype: Optional[str] = None
        self._ctrl_url: Optional[str] = None
        self._local_ip: Optional[str] = None

    # ---- 발견 ----------------------------------------------------------
    def discover(self, timeout: float = 4.0) -> bool:
        """SSDP로 IGD를 찾고 WAN 연결 서비스의 controlURL을 확보한다."""
        loc = self._ssdp_search(timeout)
        if not loc:
            logger.info("UPnP: no IGD response (router unsupported/disabled)")
            return False
        try:
            self._local_ip = self._detect_local_ip()
            self._svctype, self._ctrl_url = self._find_wan_service(loc)
        except Exception as e:
            logger.warning("UPnP: discovery parse failed: %s", e)
            return False
        if not self._ctrl_url:
            logger.info("UPnP: no WAN connection service")
            return False
        logger.info("UPnP: IGD ready (%s)", self._svctype)
        return True

    @staticmethod
    def _ssdp_search(timeout: float) -> Optional[str]:
        msg = "\r\n".join([
            "M-SEARCH * HTTP/1.1",
            "HOST: 239.255.255.250:1900",
            'MAN: "ssdp:discover"',
            "MX: 2",
            "ST: urn:schemas-upnp-org:device:InternetGatewayDevice:1",
            "", ""])
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.settimeout(timeout)
        try:
            s.sendto(msg.encode(), ("239.255.255.250", 1900))
            while True:
                data, _ = s.recvfrom(65507)
                for line in data.decode(errors="replace").splitlines():
                    if line.lower().startswith("location:"):
                        return line.split(":", 1)[1].strip()
        except socket.timeout:
            return None
        finally:
            s.close()

    @staticmethod
    def _detect_local_ip() -> str:
        sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sk.connect(("8.8.8.8", 80))
            return sk.getsockname()[0]
        finally:
            sk.close()

    @staticmethod
    def _find_wan_service(loc: str):
        base = re.match(r"(https?://[^/]+)", loc).group(1)
        xml = urllib.request.urlopen(loc, timeout=5).read()
        root = ET.fromstring(xml)
        for svc in root.iter(f"{_DEV_NS}service"):
            st = svc.findtext(f"{_DEV_NS}serviceType", "")
            if "WANIPConnection" in st or "WANPPPConnection" in st:
                ctrl = svc.findtext(f"{_DEV_NS}controlURL", "")
                ctrl_url = ctrl if ctrl.startswith("http") else base + ctrl
                return st, ctrl_url
        return None, None

    # ---- SOAP ----------------------------------------------------------
    def _soap(self, action: str, body: str) -> str:
        env = (
            '<?xml version="1.0"?>'
            '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
            f'<u:{action} xmlns:u="{self._svctype}">{body}</u:{action}>'
            '</s:Body></s:Envelope>'
        )
        req = urllib.request.Request(
            self._ctrl_url, env.encode(),
            {"Content-Type": 'text/xml; charset="utf-8"',
             "SOAPAction": f'"{self._svctype}#{action}"'},
        )
        return urllib.request.urlopen(req, timeout=6).read().decode(errors="replace")

    # ---- 매핑 ----------------------------------------------------------
    def add_mapping(self) -> bool:
        """포트를 개방한다. 성공 여부를 반환."""
        if not self._ctrl_url or not self._local_ip:
            return False
        body = (
            f"<NewRemoteHost></NewRemoteHost><NewExternalPort>{self.port}</NewExternalPort>"
            f"<NewProtocol>TCP</NewProtocol><NewInternalPort>{self.port}</NewInternalPort>"
            f"<NewInternalClient>{self._local_ip}</NewInternalClient><NewEnabled>1</NewEnabled>"
            f"<NewPortMappingDescription>{self.description}</NewPortMappingDescription>"
            f"<NewLeaseDuration>{self.lease}</NewLeaseDuration>"
        )
        try:
            self._soap("AddPortMapping", body)
            logger.info("UPnP: port %s opened -> %s:%s (lease=%ss)",
                        self.port, self._local_ip, self.port, self.lease)
            return True
        except Exception as e:
            logger.warning("UPnP: AddPortMapping failed (router refused): %s", e)
            return False

    def delete_mapping(self) -> None:
        """개방한 포트를 닫는다(종료 시 정리)."""
        if not self._ctrl_url:
            return
        body = (f"<NewRemoteHost></NewRemoteHost><NewExternalPort>{self.port}</NewExternalPort>"
                f"<NewProtocol>TCP</NewProtocol>")
        try:
            self._soap("DeletePortMapping", body)
            logger.info("UPnP: port %s mapping removed", self.port)
        except Exception as e:
            logger.debug("UPnP: DeletePortMapping failed: %s", e)

    def get_public_ip(self) -> Optional[str]:
        """외부에서 보이는 공인 IP. 라우터가 사설 IP를 보고하면(이중 NAT) echo 서비스 사용."""
        # 1) 라우터에 질의
        try:
            r = self._soap("GetExternalIPAddress", "")
            m = re.search(r"<NewExternalIPAddress>([^<]*)", r)
            ip = m.group(1) if m else ""
            if ip and not _is_private(ip):
                return ip
        except Exception:
            pass
        # 2) 외부 echo (이중 NAT 대비 — 진짜 공인 IP)
        for url in _PUBLIC_IP_ECHOS:
            try:
                ip = urllib.request.urlopen(url, timeout=5).read().decode().strip()
                if ip and not _is_private(ip):
                    return ip
            except Exception:
                continue
        return None


def _is_private(ip: str) -> bool:
    try:
        parts = [int(x) for x in ip.split(".")]
    except Exception:
        return False
    if len(parts) != 4:
        return False
    a, b = parts[0], parts[1]
    return (a == 10 or a == 127 or (a == 192 and b == 168)
            or (a == 172 and 16 <= b <= 31) or (a == 100 and 64 <= b <= 127))
