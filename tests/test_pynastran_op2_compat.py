from src.l1.pynastran_op2_compat import apply_pynastran_op2_patches


class _DummyLog:
    def __init__(self):
        self.warning_messages = []
        self.error_messages = []
        self.debug_messages = []

    def warning(self, message, *args):
        if args:
            message = message % args
        self.warning_messages.append(message)

    def error(self, message, *args):
        if args:
            message = message % args
        self.error_messages.append(message)

    def debug(self, message, *args):
        if args:
            message = message % args
        self.debug_messages.append(message)


def test_apply_pynastran_op2_patches_accepts_unknown_qualinfo_fields():
    from pyNastran.op2.result_objects.qualinfo import QualInfo

    apply_pynastran_op2_patches()
    log = _DummyLog()

    qual = QualInfo.from_str(7, "(AUXMID=3;METH=42;PARTNAME=' ')", log)

    assert qual.AUXMID == 3
    assert qual.METH == 42
    assert qual._extra_fields == {"METH": 42}
    assert any("unsupported field(s) METH" in msg for msg in log.warning_messages)
