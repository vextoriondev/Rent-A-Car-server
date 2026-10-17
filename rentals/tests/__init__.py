import json
import unittest
from contextlib import contextmanager

CONVERT_LOG=None   # set a file path here while converting old tests from wording to codes

@contextmanager
def _assert_error_code(self,code):
    """`with self.assertErrorCode('period_closed'):` passes when the block raises an error carrying that message code (M17: tests check codes, never wording)."""
    try:
        yield
    except Exception as exc:
        actual=getattr(exc,'code',None)
        if code.startswith('?') and CONVERT_LOG:   # placeholder: records the real code and the file line
            tb=exc.__traceback__
            while tb and ('tests' not in tb.tb_frame.f_code.co_filename or tb.tb_frame.f_code.co_filename.endswith('__init__.py')):tb=tb.tb_next
            with open(CONVERT_LOG,'a') as fh:
                fh.write(json.dumps({'marker':code,'actual':actual,'file':tb.tb_frame.f_code.co_filename if tb else '','line':tb.tb_lineno if tb else 0})+'\n')
            return
        self.assertEqual(actual,code,f'wrong message code: {actual!r}')
        return
    self.fail(f'no error with code {code!r} was raised')

unittest.TestCase.assertErrorCode=_assert_error_code
