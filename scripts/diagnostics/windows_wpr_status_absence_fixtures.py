"""Deterministic synthetic-only tests; no process, filesystem, or network input."""

import dataclasses
import itertools
import json
import unittest

from .windows_wpr_status_absence import (
    ABSENCE_BODY,
    ALLOWED_EXIT_CODES,
    ASCII_OUTER_WHITESPACE,
    MAX_CAPTURE_BYTES,
    Reason,
    Status,
    StatusObservation,
    classify_status_absence,
)


PHRASE = ABSENCE_BODY.encode("ascii")
BOMS = ((b"\xef\xbb\xbf", "utf-8"), (b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be"))


def observation(body=PHRASE, **changes):
    facts = dict(body=body, output_complete=True, normal_completion=True,
                 private_capture=True, exit_code=0)
    facts.update(changes)
    return StatusObservation(**facts)


class StrictStatusAbsenceTests(unittest.TestCase):
    def assert_absent(self, item):
        result = classify_status_absence(item)
        self.assertIs(result.status, Status.ABSENT)
        self.assertIs(result.reason, Reason.EXACT_ABSENCE_BODY)

    def assert_unknown(self, item, reason=None):
        result = classify_status_absence(item)
        self.assertIs(result.status, Status.UNKNOWN)
        if reason is not None:
            self.assertIs(result.reason, reason)

    def test_exact_positive_corpus_all_encodings_and_allowed_exits(self):
        wrappers = (("", ""), ("", "\n"), ("", "\r\n"),
                    (ASCII_OUTER_WHITESPACE, ASCII_OUTER_WHITESPACE))
        encodings = ((b"", "utf-8"),) + BOMS
        for index, (wrapper, encoding, exit_code) in enumerate(
            itertools.product(wrappers, encodings, sorted(ALLOWED_EXIT_CODES))
        ):
            with self.subTest(case=index):
                left, right = wrapper
                bom, codec = encoding
                data = bom + (left + ABSENCE_BODY + right).encode(codec)
                self.assert_absent(observation(data, exit_code=exit_code))

    def test_individual_ascii_outer_whitespace(self):
        for index, char in enumerate(ASCII_OUTER_WHITESPACE):
            with self.subTest(case=index):
                self.assert_absent(observation((char + ABSENCE_BODY + char).encode()))

    def test_complete_flag_required_even_for_exact_prefix(self):
        self.assert_unknown(observation(output_complete=False), Reason.OUTPUT_INCOMPLETE)

    def test_private_capture_required(self):
        self.assert_unknown(observation(private_capture=False), Reason.OUTPUT_NOT_PRIVATE)

    def test_normal_completion_required_for_timeout_failure_or_termination(self):
        for case in ("timeout", "spawn_failure", "terminated", "wait_failure"):
            with self.subTest(case=case):
                self.assert_unknown(observation(normal_completion=False), Reason.COMMAND_NOT_NORMAL)

    def test_flags_must_be_actual_booleans(self):
        for field in ("output_complete", "normal_completion", "private_capture"):
            for index, value in enumerate((0, 1, "True", "False", None, (), [])):
                with self.subTest(field=field, case=index):
                    self.assert_unknown(observation(**{field: value}), Reason.INVALID_INPUT)

    def test_exit_code_type_is_strict(self):
        for index, value in enumerate((False, True, 0.0, "0", "0xC5583000", b"0", [])):
            with self.subTest(case=index):
                self.assert_unknown(observation(exit_code=value), Reason.INVALID_INPUT)

    def test_exit_codes_do_not_alone_prove_absence(self):
        for exit_code in sorted(ALLOWED_EXIT_CODES):
            with self.subTest(exit_code=exit_code):
                self.assert_unknown(observation(b"", exit_code=exit_code), Reason.BODY_NOT_EXACT)
                self.assert_unknown(observation(b"Recording in progress", exit_code=exit_code), Reason.BODY_NOT_EXACT)

    def test_nonallowlisted_exit_codes(self):
        codes = (None, 1, 255, -1, 0xC5583000 - (1 << 32), 0xC5583001,
                 0xC5583014, 0xFFFFFFFF, 1 << 32, (1 << 32) + 0xC5583000)
        for index, code in enumerate(codes):
            with self.subTest(case=index):
                self.assert_unknown(observation(exit_code=code), Reason.EXIT_NOT_ALLOWED)

    def test_capture_at_exact_byte_bound_is_permitted(self):
        data = b" " * (MAX_CAPTURE_BYTES - len(PHRASE)) + PHRASE
        self.assertEqual(len(data), MAX_CAPTURE_BYTES)
        self.assert_absent(observation(data))

    def test_capture_over_byte_bound_is_rejected_before_decode(self):
        data = b" " * (MAX_CAPTURE_BYTES + 1 - len(PHRASE)) + PHRASE
        self.assert_unknown(observation(data), Reason.OUTPUT_OVERSIZE)
        self.assert_unknown(observation(b"\xff" * (MAX_CAPTURE_BYTES + 1)), Reason.OUTPUT_OVERSIZE)

    def test_extra_or_changed_body_negative_corpus(self):
        bodies = (
            "", " \t\r\n\v\f", "WPR is recording", "Recording in progress",
            "WPR is not recording.", "wpr is not recording", "WPR IS NOT RECORDING",
            "WPR  is not recording", "WPR is not\nrecording", "WPR is not recording!",
            '"WPR is not recording"', "'WPR is not recording'",
            "prefix WPR is not recording", "WPR is not recording suffix",
            "Banner\r\nWPR is not recording", "WPR is not recording\r\nActive session",
            "WPR is not recording\nWPR is not recording",
            "WPR is not recording\nC:\\private\\trace.etl",
            "C:\\private\\WPR is not recording\\trace.etl",
            "WPR is not recording\nSessionId: synthetic-session-123",
            "WPR is not recording\n0xC5583014: Collector in use",
            "WPR ne procède pas à l’enregistrement", "WPR 未在录制",
            "WPR is not recording\x00", "\x00WPR is not recording",
        )
        for index, (body, exit_code) in enumerate(itertools.product(bodies, sorted(ALLOWED_EXIT_CODES))):
            with self.subTest(case=index):
                self.assert_unknown(observation(body.encode(), exit_code=exit_code), Reason.BODY_NOT_EXACT)

    def test_unicode_whitespace_is_not_stripped(self):
        for index, char in enumerate(("\u0085", "\u00a0", "\u1680", "\u2000", "\u200b", "\u2028", "\u2029", "\u202f", "\u3000")):
            with self.subTest(case=index):
                self.assert_unknown(observation((char + ABSENCE_BODY + char).encode()), Reason.BODY_NOT_EXACT)

    def test_repeated_or_mixed_initial_boms_are_rejected(self):
        for index, (first, second) in enumerate(itertools.product(BOMS, repeat=2)):
            with self.subTest(case=index):
                self.assert_unknown(observation(first[0] + second[0] + PHRASE), Reason.REPEATED_BOM)

    def test_nonleading_or_internal_bom_is_not_removed(self):
        for index, text in enumerate((" \ufeff" + ABSENCE_BODY, ABSENCE_BODY + "\ufeff", "WPR\ufeff is not recording")):
            with self.subTest(case=index):
                self.assert_unknown(observation(text.encode()), Reason.BODY_NOT_EXACT)
                self.assert_unknown(observation(b"\xef\xbb\xbf" + text.encode()), Reason.BODY_NOT_EXACT)

    def test_utf32_bom_prefix_collision_is_explicitly_rejected(self):
        for index, (bom, codec) in enumerate(((b"\xff\xfe\x00\x00", "utf-32-le"), (b"\x00\x00\xfe\xff", "utf-32-be"))):
            with self.subTest(case=index):
                self.assert_unknown(observation(bom + ABSENCE_BODY.encode(codec)), Reason.ENCODING_INVALID)

    def test_unmarked_utf16_is_never_guessed(self):
        for codec in ("utf-16-le", "utf-16-be"):
            with self.subTest(codec=codec):
                self.assert_unknown(observation(ABSENCE_BODY.encode(codec)), Reason.BODY_NOT_EXACT)

    def test_unmarked_utf32_is_not_accepted(self):
        for codec in ("utf-32-le", "utf-32-be"):
            with self.subTest(codec=codec):
                self.assert_unknown(observation(ABSENCE_BODY.encode(codec)), Reason.BODY_NOT_EXACT)

    def test_invalid_encoding_never_uses_replacement_or_fallback(self):
        bodies = (
            b"\xff" + PHRASE, PHRASE + b"\x80", PHRASE + b"\xc2", b"\xc0\xaf" + PHRASE,
            b"\xef\xbb", b"\xef\xbb\xbf" + PHRASE + b"\xff",
            b"\xef\xbb\xbf\xed\xa0\x80", b"\xf4\x90\x80\x80",
            b"\xff\xfe" + ABSENCE_BODY.encode("utf-16-le") + b"\x00",
            b"\xfe\xff" + ABSENCE_BODY.encode("utf-16-be") + b"\x00",
            b"\xff\xfe\x00\xd8", b"\xfe\xff\xd8\x00",
        )
        for index, body in enumerate(bodies):
            with self.subTest(case=index):
                self.assert_unknown(observation(body), Reason.ENCODING_INVALID)

    def test_wrong_endian_bom_cannot_confirm_absence(self):
        self.assert_unknown(observation(b"\xff\xfe" + ABSENCE_BODY.encode("utf-16-be")))
        self.assert_unknown(observation(b"\xfe\xff" + ABSENCE_BODY.encode("utf-16-le")))

    def test_bom_without_body_is_unknown(self):
        for index, (bom, _codec) in enumerate(BOMS):
            with self.subTest(case=index):
                self.assert_unknown(observation(bom), Reason.BODY_NOT_EXACT)

    def test_every_phrase_truncation_is_unknown(self):
        for length in range(len(PHRASE)):
            with self.subTest(length=length):
                self.assert_unknown(observation(PHRASE[:length]))

    def test_exhaustive_single_byte_insertions(self):
        permitted = set(ASCII_OUTER_WHITESPACE.encode())
        for position in range(len(PHRASE) + 1):
            for value in range(256):
                with self.subTest(position=position, byte=value):
                    item = observation(PHRASE[:position] + bytes((value,)) + PHRASE[position:])
                    if position in (0, len(PHRASE)) and value in permitted:
                        self.assert_absent(item)
                    else:
                        self.assert_unknown(item)

    def test_exhaustive_single_byte_substitutions(self):
        for position, original in enumerate(PHRASE):
            for value in range(256):
                with self.subTest(position=position, byte=value):
                    item = observation(PHRASE[:position] + bytes((value,)) + PHRASE[position + 1:])
                    if value == original:
                        self.assert_absent(item)
                    else:
                        self.assert_unknown(item)

    def test_input_shape_is_strict(self):
        for index, item in enumerate((None, {}, PHRASE, "WPR is not recording", object())):
            with self.subTest(case=index):
                self.assert_unknown(item, Reason.INVALID_INPUT)
        for index, data in enumerate((None, "WPR is not recording", bytearray(PHRASE), memoryview(PHRASE))):
            with self.subTest(body_case=index):
                self.assert_unknown(observation(data), Reason.INVALID_INPUT)

    def test_reason_precedence_is_deterministic(self):
        facts = observation(body=b"\xff" * (MAX_CAPTURE_BYTES + 1), private_capture=False,
                            normal_completion=False, output_complete=False, exit_code=1)
        self.assert_unknown(facts, Reason.OUTPUT_NOT_PRIVATE)
        facts = dataclasses.replace(facts, private_capture=True)
        self.assert_unknown(facts, Reason.COMMAND_NOT_NORMAL)
        facts = dataclasses.replace(facts, normal_completion=True)
        self.assert_unknown(facts, Reason.OUTPUT_INCOMPLETE)
        facts = dataclasses.replace(facts, output_complete=True)
        self.assert_unknown(facts, Reason.OUTPUT_OVERSIZE)
        facts = dataclasses.replace(facts, body=b"\xff")
        self.assert_unknown(facts, Reason.EXIT_NOT_ALLOWED)
        facts = dataclasses.replace(facts, exit_code=0)
        self.assert_unknown(facts, Reason.ENCODING_INVALID)

    def test_diagnostics_are_closed_set_and_do_not_retain_raw_input(self):
        item = observation(b"WPR is not recording\nC:\\private\\synthetic-session-123.etl")
        result = classify_status_absence(item)
        self.assertEqual(repr(item), "StatusObservation()")
        self.assertEqual(result.public_record(), {"status": "UNKNOWN", "reason": "body_not_exact"})
        self.assertEqual(set(result.public_record()), {"status", "reason"})
        self.assertEqual([field.name for field in dataclasses.fields(result)], ["status", "reason"])
        self.assertEqual(json.loads(json.dumps(result.public_record())), result.public_record())
        self.assertIn(result.public_record()["status"], {member.value for member in Status})
        self.assertIn(result.public_record()["reason"], {member.value for member in Reason})

    def test_observation_and_decision_are_immutable(self):
        item = observation()
        result = classify_status_absence(item)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            item.output_complete = False
        with self.assertRaises(dataclasses.FrozenInstanceError):
            result.reason = Reason.BODY_NOT_EXACT


if __name__ == "__main__":
    unittest.main(verbosity=2)
