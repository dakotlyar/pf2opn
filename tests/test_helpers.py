"""Unit tests for every §7.3 helper in ``translators/base.py``."""

from __future__ import annotations

from types import SimpleNamespace

from lxml import etree

from pf2opn.translators.base import (
    E,
    fix_port,
    map_net,
    net_deps,
    opn_bool,
    pf_desc,
    pf_endpoint,
    pf_flag,
    pf_list,
    pf_text,
    sanitize_ifdescr,
    sanitize_name,
    sub,
)


def _el(xml: str) -> etree._Element:
    return etree.fromstring(xml)


def test_pf_text() -> None:
    el = _el("<a><b>  hi  </b><empty/></a>")
    assert pf_text(el, "b") == "hi"
    assert pf_text(el, "empty") == ""
    assert pf_text(el, "missing") == ""
    assert pf_text(el, "missing", "fallback") == "fallback"
    assert pf_text(None, "b") == ""


def test_pf_desc_unescapes_flattens_truncates() -> None:
    el = _el("<a><descr>a &amp;amp; b&#10;c</descr></a>")
    assert pf_desc(el) == "a & b c"
    assert pf_desc(el, "missing") == ""

    long = _el(f"<a><descr>{'x' * 300}</descr></a>")
    assert len(pf_desc(long)) == 255


def test_pf_flag() -> None:
    el = _el(
        "<a><enable/><off>off</off><no>no</no><zero>0</zero>"
        "<false>false</false><yes>yes</yes></a>"
    )
    assert pf_flag(el, "enable") is True
    assert pf_flag(el, "off") is False
    assert pf_flag(el, "no") is False
    assert pf_flag(el, "zero") is False
    assert pf_flag(el, "false") is False
    assert pf_flag(el, "yes") is True
    assert pf_flag(el, "missing") is False
    assert pf_flag(None, "x") is False


def test_pf_list() -> None:
    el = _el("<a><s>one</s><s> two </s><s></s><s>three</s></a>")
    assert pf_list(el, "s") == ["one", "two", "three"]
    assert pf_list(el, "missing") == []


def test_opn_bool() -> None:
    assert opn_bool(True) == "1"
    assert opn_bool(False) == "0"


def test_E() -> None:
    el = E("tag", "text", uuid="abc", n=1)
    assert el.tag == "tag"
    assert el.text == "text"
    assert el.get("uuid") == "abc"
    assert el.get("n") == "1"


def test_sub() -> None:
    parent = E("parent")
    empty = sub(parent, "child")
    full = sub(parent, "other", "value")
    assert empty.text is None
    assert full.text == "value"
    assert parent.find("child") is empty


def test_fix_port() -> None:
    assert fix_port("any-1024") == "1-1024"
    assert fix_port("1024-any") == "1024-65535"
    assert fix_port("80") == "80"
    assert fix_port("") == ""
    assert fix_port("80:90") == "80:90"
    assert fix_port("80:90", colon_to_dash=True) == "80-90"


def test_sanitize_ifdescr() -> None:
    assert sanitize_ifdescr("LAN & DMZ") == "LAN___DMZ"
    assert sanitize_ifdescr("ok_1") == "ok_1"
    assert sanitize_ifdescr("") == ""


def test_sanitize_name() -> None:
    assert sanitize_name("my wg/name") == "my_wg_name"
    assert sanitize_name("a.b-c_d") == "a.b-c_d"
    assert sanitize_name("x" * 100) == "x" * 64
    assert sanitize_name("x" * 100, maxlen=10) == "x" * 10


def test_map_net(minimal_plan) -> None:
    _src, plan = minimal_plan
    ifmap = plan.ifmap
    assert map_net("any", ifmap) == ("any", None)
    assert map_net("", ifmap) == ("any", None)
    assert map_net("(self)", ifmap) == ("(self)", None)
    assert map_net("10.0.0.0/8", ifmap) == ("10.0.0.0/8", None)
    assert map_net("192.168.1.5", ifmap) == ("192.168.1.5", None)
    assert map_net("lan", ifmap) == ("lan", None)
    assert map_net("lanip", ifmap) == ("lanip", None)
    assert map_net("somealias", ifmap) == ("somealias", None)

    ifmap.targets["lan"].include = False
    assert map_net("lan", ifmap) == (None, "interface lan not migrated")
    assert map_net("lanip", ifmap) == (None, "interface lan not migrated")


def test_net_deps(minimal_plan) -> None:
    src, _plan = minimal_plan
    context = SimpleNamespace(alias_names={"somealias", "otheralias"}, pf_ifaces=src.pf_ifaces)
    assert net_deps("somealias", context) == ["alias:somealias"]
    assert net_deps("lan", context) == ["iface:lan"]
    assert net_deps("lanip", context) == ["iface:lan"]
    assert net_deps("10.0.0.1", context) == []
    assert net_deps("any", context) == []
    assert net_deps("(self)", context) == []
    assert net_deps("unknown", context) == []


def test_pf_endpoint() -> None:
    assert pf_endpoint(_el("<source><any/></source>")) == ("any", False, "")
    assert pf_endpoint(
        _el("<destination><network>lan</network><port>80</port><not/></destination>")
    ) == ("lan", True, "80")
    assert pf_endpoint(_el("<source><address>1.2.3.4</address></source>")) == (
        "1.2.3.4",
        False,
        "",
    )
    assert pf_endpoint(_el("<source><any/><port>any-1024</port></source>")) == (
        "any",
        False,
        "1-1024",
    )
    assert pf_endpoint(None) == ("any", False, "")