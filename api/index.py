import os
import sys

# Add the project root directory to the Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app import app

import traceback
from html import escape
from werkzeug.exceptions import HTTPException


def _is_general_manager():
    try:
        from flask_login import current_user
        return bool(current_user.is_authenticated and current_user.role in ('general_manager', 'owner'))
    except Exception:
        return False


@app.errorhandler(Exception)
def unhandled_exception(e):
    # أخطاء HTTP العادية (404، 403، 405...) تفضل زي ما هي
    if isinstance(e, HTTPException):
        return e

    tb = traceback.format_exc()
    # التفاصيل الكاملة بتتسجل في لوج Vercel دايماً
    print(tb, file=sys.stderr)

    try:
        from backend.core import db
        db.session.rollback()
    except Exception:
        pass

    # المدير العام بس هو اللي يشوف تفاصيل الخطأ على الشاشة
    if _is_general_manager():
        return "<pre dir='ltr'>" + escape(tb) + "</pre>", 500

    return (
        "<div dir='rtl' style='font-family:Tahoma,sans-serif;text-align:center;padding:60px 16px'>"
        "<h2>حدث خطأ غير متوقع</h2>"
        "<p>حاول مرة تانية، ولو المشكلة استمرت بلغ المدير العام.</p>"
        "<a href='/'>الرجوع للرئيسية</a></div>"
    ), 500

# This is required for Vercel
# The variable name must be 'app'
