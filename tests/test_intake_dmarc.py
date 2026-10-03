"""`intake.dmarc_verified`: true only for Tom's mail that Gmail's own
topmost `Authentication-Results` line passes for DMARC, aligned with the
one `From` address."""

import pytest

from core import intake

pytestmark = pytest.mark.spend(usd=0)

GOOGLE = "mx.google.com"
PASS = (
    "mx.google.com;\r\n       dkim=pass header.i=@yuda.me header.s=google header.b=abc;\r\n"
    "       spf=pass (google.com: domain of tom@yuda.me designates 1.2.3.4 as permitted sender)"
    " smtp.mailfrom=tom@yuda.me;\r\n       dmarc=pass (p=REJECT sp=REJECT dis=NONE) header.from=yuda.me"
)


def headers(froms, results):
    return {"from": froms, "authentication_results": results}


@pytest.mark.parametrize("sender", ["Tom <tom@yuda.me>", "TOM@YUDA.ME", "Tom Counsell <Tom@Yuda.Me>"])
def test_a_single_from_passing_at_the_topmost_google_line_is_verified(sender):
    assert intake.dmarc_verified(headers([sender], [PASS]), GOOGLE)
    assert intake.dmarc_verified(
        headers([sender], [PASS.replace("header.from=yuda.me", "header.from=YUDA.ME")]), GOOGLE
    )


@pytest.mark.parametrize("result", ["dmarc=fail", "dmarc=none", "dmarc=temperror", None])
def test_anything_but_a_dmarc_pass_is_not_verified(result):
    line = PASS.replace("dmarc=pass", result) if result else PASS.split("dmarc=")[0].rstrip(" \r\n;")
    assert not intake.dmarc_verified(headers(["tom@yuda.me"], [line]), GOOGLE)


def test_a_forged_pass_below_a_real_topmost_fail_is_not_verified():
    fail = PASS.replace("dmarc=pass", "dmarc=fail")
    assert not intake.dmarc_verified(headers(["tom@yuda.me"], [fail, PASS]), GOOGLE)


@pytest.mark.parametrize("authserv", ["mx.evil.example", "mx.google.com.evil.example", "google.com"])
def test_a_pass_from_any_other_authserv_id_is_not_verified(authserv):
    forged = PASS.replace("mx.google.com;", f"{authserv};", 1)
    assert not intake.dmarc_verified(headers(["tom@yuda.me"], [forged]), GOOGLE)
    assert not intake.dmarc_verified(headers(["tom@yuda.me"], [forged, PASS]), GOOGLE)


def test_a_pass_for_another_domain_than_the_from_is_not_verified():
    other = PASS.replace("header.from=yuda.me", "header.from=evil.example")
    assert not intake.dmarc_verified(headers(["tom@yuda.me"], [other]), GOOGLE)


@pytest.mark.parametrize(
    "froms",
    [
        ["tom@yuda.me, other@evil.example"],
        ["tom@yuda.me", "tom@yuda.me"],
        ['"tom@yuda.me" <other@x>'],
        ["Tom <tom@yuda.me@x>"],
        [],
    ],
)
def test_a_from_that_is_not_one_address_of_the_passing_domain_is_not_verified(froms):
    assert not intake.dmarc_verified(headers(froms, [PASS]), GOOGLE)


def test_a_display_name_trick_is_judged_by_the_address():
    # The address is other@x; Gmail passes DMARC for x, so the record is
    # verified for x, never for yuda.me.
    trick = ['"tom@yuda.me" <other@x>']
    assert intake.dmarc_verified(
        headers(trick, [PASS.replace("header.from=yuda.me", "header.from=x")]), GOOGLE
    )
    assert not intake.dmarc_verified(headers(trick, [PASS]), GOOGLE)


def test_no_authentication_results_is_not_verified():
    assert not intake.dmarc_verified(headers(["tom@yuda.me"], []), GOOGLE)
    assert not intake.dmarc_verified({"from": ["tom@yuda.me"]}, GOOGLE)


def test_comments_cannot_carry_a_pass():
    commented = PASS.replace("dmarc=pass (p=REJECT sp=REJECT dis=NONE)", "dmarc=fail (dmarc=pass)")
    assert not intake.dmarc_verified(headers(["tom@yuda.me"], [commented]), GOOGLE)
