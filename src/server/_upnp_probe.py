"""UPnP IGD 자동 포트개방 가능 여부 진단 (stdlib only).

라우터(IGD)를 SSDP로 찾고, WAN 연결 서비스에 AddPortMapping 을 실제로 시도해
cloudflared 없이 자동 포트개방이 되는지 확인한다.

  python _upnp_probe.py [external_port] [internal_port]
"""
import re
import socket
import sys
import urllib.request
from xml.etree import ElementTree as ET

UPNP_DEV_NS = "{urn:schemas-upnp-org:device-1-0}"


def discover():
    msg = "\r\n".join([
        "M-SEARCH * HTTP/1.1",
        "HOST: 239.255.255.250:1900",
        'MAN: "ssdp:discover"',
        "MX: 2",
        "ST: urn:schemas-upnp-org:device:InternetGatewayDevice:1",
        "", ""])
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.settimeout(4)
    s.sendto(msg.encode(), ("239.255.255.250", 1900))
    try:
        while True:
            data, _ = s.recvfrom(65507)
            for line in data.decode(errors="replace").splitlines():
                if line.lower().startswith("location:"):
                    return line.split(":", 1)[1].strip()
    except socket.timeout:
        return None


def local_ip():
    sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sk.connect(("8.8.8.8", 80))
    ip = sk.getsockname()[0]
    sk.close()
    return ip


def find_wan_service(loc):
    base = re.match(r"(https?://[^/]+)", loc).group(1)
    xml = urllib.request.urlopen(loc, timeout=5).read()
    root = ET.fromstring(xml)
    for svc in root.iter(f"{UPNP_DEV_NS}service"):
        st = svc.findtext(f"{UPNP_DEV_NS}serviceType", "")
        if "WANIPConnection" in st or "WANPPPConnection" in st:
            ctrl = svc.findtext(f"{UPNP_DEV_NS}controlURL", "")
            ctrl_url = ctrl if ctrl.startswith("http") else base + ctrl
            return st, ctrl_url
    return None, None


def soap(ctrl_url, svctype, action, body):
    env = (
        '<?xml version="1.0"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
        f'<u:{action} xmlns:u="{svctype}">{body}</u:{action}>'
        '</s:Body></s:Envelope>'
    )
    req = urllib.request.Request(
        ctrl_url, env.encode(),
        {"Content-Type": 'text/xml; charset="utf-8"',
         "SOAPAction": f'"{svctype}#{action}"'},
    )
    return urllib.request.urlopen(req, timeout=6).read().decode(errors="replace")


def main():
    ext = int(sys.argv[1]) if len(sys.argv) > 1 else 9700
    intp = int(sys.argv[2]) if len(sys.argv) > 2 else 9700
    loc = discover()
    if not loc:
        print("UPnP: 라우터 응답 없음 (미지원/비활성)")
        return
    print("IGD LOCATION:", loc)
    myip = local_ip()
    print("local IP:", myip)
    svctype, ctrl_url = find_wan_service(loc)
    if not ctrl_url:
        print("WAN 연결 서비스 없음")
        return
    print("service:", svctype)

    try:
        r = soap(ctrl_url, svctype, "GetExternalIPAddress", "")
        m = re.search(r"<NewExternalIPAddress>([^<]*)", r)
        print("external IP:", m.group(1) if m else "?")
    except Exception as e:
        print("GetExternalIPAddress 실패:", e)

    body = (
        f"<NewRemoteHost></NewRemoteHost><NewExternalPort>{ext}</NewExternalPort>"
        f"<NewProtocol>TCP</NewProtocol><NewInternalPort>{intp}</NewInternalPort>"
        f"<NewInternalClient>{myip}</NewInternalClient><NewEnabled>1</NewEnabled>"
        f"<NewPortMappingDescription>qonvo-test</NewPortMappingDescription>"
        f"<NewLeaseDuration>120</NewLeaseDuration>"
    )
    try:
        soap(ctrl_url, svctype, "AddPortMapping", body)
        print(f"AddPortMapping: OK ({ext} TCP -> {myip}:{intp}, 120초 임시)")
        chk = soap(ctrl_url, svctype, "GetSpecificPortMappingEntry",
                   f"<NewRemoteHost></NewRemoteHost><NewExternalPort>{ext}</NewExternalPort><NewProtocol>TCP</NewProtocol>")
        print("확인:", "매핑 존재 OK" if myip in chk else chk[:200])
        print("\n=> UPnP 자동개방 실제로 됨! cloudflared 없이 가능")
    except Exception as e:
        print("AddPortMapping 실패:", e)
        print("\n=> 라우터가 자동개방 거부 → cloudflared 또는 수동 포워딩 필요")


if __name__ == "__main__":
    main()
