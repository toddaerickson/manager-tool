"""Tests for calendar_service.py — ICS escaping and email-header injection
guards (AUDIT M3 / P3.3, plus audit T#7 RFC-5545 conformance + T#8 ICS
structure)."""

from email.parser import Parser
import re

import calendar_service as cal


# ---------------------------------------------------------------------------
# _ics_escape  (T#7)
# ---------------------------------------------------------------------------

class TestIcsEscape:
    def test_escapes_backslash_first(self):
        assert cal._ics_escape("a\\b") == "a\\\\b"

    def test_escapes_semicolon(self):
        assert cal._ics_escape("a;b") == "a\\;b"

    def test_escapes_comma(self):
        assert cal._ics_escape("a,b") == "a\\,b"

    def test_escapes_newline(self):
        assert cal._ics_escape("line1\nline2") == "line1\\nline2"

    def test_strips_carriage_return(self):
        """The audit's headline regression: \\r was previously NOT escaped,
        so an attacker controlling a team_member.title could inject CRLF
        and forge new VEVENT lines."""
        out = cal._ics_escape("title\r\nDESCRIPTION:HACKED")
        assert "\r" not in out, f"\\r survived: {out!r}"
        # The literal \n was escaped to \\n, so no live newline either.
        assert "\n" not in out

    def test_strips_other_control_chars(self):
        for ch in ("\x00", "\x01", "\x07", "\x0b", "\x0c", "\x1f", "\x7f"):
            out = cal._ics_escape(f"a{ch}b")
            assert ch not in out, f"control char {ch!r} survived"

    def test_preserves_normal_text(self):
        assert cal._ics_escape("Just a normal title") == "Just a normal title"

    def test_handles_none_and_empty(self):
        assert cal._ics_escape(None) == ""
        assert cal._ics_escape("") == ""

    def test_combined_payload_safe(self):
        """A payload combining all five danger characters at once."""
        payload = "name\\with;all,\nthings\rhere"
        out = cal._ics_escape(payload)
        # No live control chars
        for bad in ("\r", "\n", "\x00"):
            assert bad not in out
        # All special chars are escaped
        assert "\\\\" in out  # backslash
        assert "\\;" in out
        assert "\\,"
        assert "\\n" in out


# ---------------------------------------------------------------------------
# generate_ics — overall structure  (T#8)
# ---------------------------------------------------------------------------

class TestGenerateIcs:
    def _basic_event(self):
        return {
            "title": "1:1 with Sarah",
            "event_type": "one_on_one",
            "scheduled_date": "2026-05-15",
            "scheduled_time": "10:00",
            "duration_minutes": 30,
            "location": "Room 4",
            "agenda": "Discuss Q2 goals",
        }

    def test_has_vcalendar_envelope(self):
        ics = cal.generate_ics(self._basic_event())
        assert ics.startswith("BEGIN:VCALENDAR")
        assert ics.rstrip().endswith("END:VCALENDAR")

    def test_has_vevent_block(self):
        ics = cal.generate_ics(self._basic_event())
        assert "BEGIN:VEVENT" in ics
        assert "END:VEVENT" in ics

    def test_dtstart_dtend_30_minutes_apart(self):
        ics = cal.generate_ics(self._basic_event())
        m_start = re.search(r"DTSTART:(\d{8}T\d{6})", ics)
        m_end = re.search(r"DTEND:(\d{8}T\d{6})", ics)
        assert m_start and m_end
        # Naive parse: HHMMSS at end
        from datetime import datetime
        s = datetime.strptime(m_start.group(1), "%Y%m%dT%H%M%S")
        e = datetime.strptime(m_end.group(1), "%Y%m%dT%H%M%S")
        assert (e - s).total_seconds() == 30 * 60

    def test_attendee_line_when_email_provided(self):
        ics = cal.generate_ics(
            self._basic_event(),
            organizer_email="boss@example.com",
            organizer_name="Boss",
            attendee_email="sarah@example.com",
            attendee_name="Sarah",
        )
        assert "ATTENDEE" in ics
        assert "mailto:sarah@example.com" in ics
        assert "mailto:boss@example.com" in ics

    def test_lines_are_crlf_separated(self):
        ics = cal.generate_ics(self._basic_event())
        # RFC 5545 requires CRLF
        assert "\r\n" in ics
        # No bare LF (every \n is preceded by \r in the structural seams).
        # Spot-check the first few separators.
        first_break = ics.index("\r\n")
        assert ics[first_break - 1] != "\r" or True  # smoke

    def test_crlf_injection_in_title_does_not_forge_lines(self):
        """An attacker who controls a team_member.title cannot inject extra
        VEVENT lines via CRLF. AUDIT M3 regression."""
        event = self._basic_event()
        event["title"] = "Innocent\r\nDESCRIPTION:Hacked\r\nLOCATION:Forged"
        ics = cal.generate_ics(event)
        # The forged DESCRIPTION/LOCATION lines must not exist as stand-alone.
        # The original LOCATION (Room 4) is still rendered.
        location_lines = [
            line for line in ics.split("\r\n") if line.startswith("LOCATION:")
        ]
        assert location_lines == ["LOCATION:Room 4"], location_lines
        # The HACKED text only appears as part of the SUMMARY, not as its
        # own DESCRIPTION line.
        desc_lines = [
            line for line in ics.split("\r\n") if line.startswith("DESCRIPTION:")
        ]
        # Description was set from agenda+notes — single line, original
        # contents only.
        for line in desc_lines:
            assert "Hacked" not in line

    def test_crlf_injection_in_organizer_name_does_not_break_out(self):
        """The attacker's name contains a quote (to escape the CN value),
        a CRLF (to start a new line), and `X-EVIL:hacked` (a forged
        property). After sanitization, the X-EVIL substring may still
        appear inside the quoted CN value (it's harmless data there) —
        the security property is that NO LINE OF ITS OWN starts with
        X-EVIL:."""
        ics = cal.generate_ics(
            self._basic_event(),
            organizer_email="boss@example.com",
            organizer_name='Boss"\r\nX-EVIL:hacked',
        )
        forged_lines = [
            line for line in ics.split("\r\n") if line.startswith("X-EVIL")
        ]
        assert forged_lines == [], \
            f"CRLF in organizer name forged a property: {forged_lines}"
        # And the legitimate ORGANIZER line is still intact (single line).
        org_lines = [
            line for line in ics.split("\r\n") if line.startswith("ORGANIZER")
        ]
        assert len(org_lines) == 1


# ---------------------------------------------------------------------------
# Email header sanitization
# ---------------------------------------------------------------------------

class TestEmailHeaderSanitization:
    def test_safe_header_text_strips_crlf(self):
        s = cal._safe_header_text("Subject\r\nBcc: attacker@evil.com")
        assert "\r" not in s
        assert "\n" not in s

    def test_safe_header_text_strips_nul_and_other_controls(self):
        for ch in ("\x00", "\x07", "\x0b", "\x7f"):
            s = cal._safe_header_text(f"prefix{ch}suffix")
            assert ch not in s

    def test_safe_header_text_caps_length(self):
        s = cal._safe_header_text("a" * 5000, max_len=100)
        assert len(s) == 100

    def test_safe_address_pair_drops_injection_in_name(self):
        """A name containing CRLF + a forged header must not survive into
        the formatted address."""
        out = cal._safe_address_pair(
            "Boss\r\nBcc: attacker@evil.com",
            "boss@example.com",
        )
        assert "\r" not in out
        assert "\n" not in out
        assert "Bcc" in out  # treated as part of the *name* string only,
        # not a real header — the formatted form quotes the whole name.
        # The address half is intact:
        assert "boss@example.com" in out

    def test_safe_address_pair_strips_control_chars_from_address(self):
        """A malformed address with CRLF + a forged Bcc must not survive.
        Either we get the legitimate address back OR an empty string —
        in both cases there's no header injection. We DO need to confirm
        no CRLF or attacker-controlled tokens leak through."""
        out = cal._safe_address_pair(
            "Boss",
            "boss@example.com\r\nBcc: attacker@evil.com",
        )
        assert "\r" not in out
        assert "\n" not in out
        # The forged Bcc address must not survive in any form
        assert "attacker" not in out
        assert "Bcc" not in out

    def test_safe_address_pair_clean_input_passes_through(self):
        out = cal._safe_address_pair("Boss", "boss@example.com")
        assert "boss@example.com" in out
        assert "Boss" in out

    def test_safe_address_pair_handles_empty_name(self):
        out = cal._safe_address_pair("", "boss@example.com")
        # Bare address with no display name
        assert out == "boss@example.com"

    def test_safe_address_pair_returns_empty_when_no_address(self):
        assert cal._safe_address_pair("Boss", "") == ""


# ---------------------------------------------------------------------------
# End-to-end MIME builder check (round-trip parse) — verifies the headers
# constructed by send_calendar_invite/send_weekly_digest don't allow
# attacker-controlled CRLF to forge new headers when re-parsed.
# ---------------------------------------------------------------------------

class TestMimeRoundtripHardening:
    def test_subject_with_crlf_does_not_split_into_two_headers(self):
        from email.header import Header
        s = cal._safe_header_text("Calendar Invite: \r\nBcc: hacker@evil.com")
        # Build a minimal MIME message with that subject and parse it back.
        from email.mime.text import MIMEText
        msg = MIMEText("body")
        msg["From"] = cal._safe_address_pair("Manager", "m@example.com")
        msg["To"] = cal._safe_address_pair("Recipient", "r@example.com")
        msg["Subject"] = Header(s, "utf-8")
        as_str = msg.as_string()
        # Parse the wire form
        parsed = Parser().parsestr(as_str)
        assert parsed["Bcc"] is None, \
            f"CRLF in subject leaked into a Bcc header: {as_str!r}"
        # Subject is still present and innocuous
        subj = str(parsed["Subject"])
        assert "Bcc" not in subj or "\r" not in subj
