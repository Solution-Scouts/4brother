import os
import requests
from datetime import datetime
from flask import Flask, redirect, Response
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
import pytz
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")

app = Flask(__name__, template_folder=TEMPLATES_DIR, static_folder=STATIC_DIR)
# مفتاح تأمين الجلسات: لازم يتحط في متغيرات البيئة (Vercel + .env المحلي)
_secret_key = os.environ.get('SECRET_KEY', '').strip()
if not _secret_key:
    # من غير SECRET_KEY بيتعمل مفتاح عشوائي مؤقت: النظام يشتغل، بس الدخول بيتمسح مع كل تشغيل جديد للسيرفر.
    # (ما فيش مفتاح ثابت مكتوب في الكود علشان محدش يقدر يزوّر جلسة دخول)
    import secrets as _secrets
    print("[SECURITY WARNING] SECRET_KEY is not set in environment; using a random temporary key.")
    _secret_key = _secrets.token_hex(32)
app.config['SECRET_KEY'] = _secret_key
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['REMEMBER_COOKIE_HTTPONLY'] = True
app.config['REMEMBER_COOKIE_SAMESITE'] = 'Lax'

basedir = BASE_DIR
db_url = os.environ.get('DATABASE_URL')
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)
app.config['SQLALCHEMY_DATABASE_URI'] = db_url or 'sqlite:///' + os.path.join(BASE_DIR, 'erp_crm.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join(STATIC_DIR, 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

try:
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])
except OSError:
    pass  # Read-only filesystem (like Vercel)

app.jinja_env.globals.update(now=datetime.now)

def get_image_url(filename):
    if not filename:
        return "/static/default_product.png"
    filename_str = str(filename).strip()
    if filename_str.startswith('http'):
        # روابط Cloudinary القديمة المعطلة (الحساب القديم le5kl7fy فقط)
        if 'le5kl7fy' in filename_str:
            return "/static/default_product.png"
        # روابط Cloudinary الجديدة وروابط الصور الخارجية تفتح مباشرة بدون أي استهلاك لمعالج Vercel
        return filename_str
    if filename_str.startswith('/'):
        return filename_str
    return f"/static/uploads/{filename}"

@app.template_filter('image_url')
def image_url(filename):
    return get_image_url(filename)

@app.route('/cdn-images/<path:filename>')
def cdn_image_proxy(filename):
    # مسار احتياطي يعيد التوجيه للصورة الافتراضية فوراً دون استهلاك وقت معالج
    return redirect('/static/default_product.png', code=302)

from sqlalchemy.sql import expression
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.types import String

class to_char(expression.FunctionElement):
    type = String()
    name = 'to_char'

@compiles(to_char, 'sqlite')
def sqlite_to_char(element, compiler, **kw):
    args = list(element.clauses)
    if len(args) == 2:
        col = compiler.process(args[0], **kw)
        fmt = compiler.process(args[1], **kw)
        if fmt in ("'YYYY-MM'", '"YYYY-MM"'):
            return f"strftime('%Y-%m', {col})"
        elif fmt in ("'YYYY-MM-DD'", '"YYYY-MM-DD"'):
            return f"date({col})"
    return compiler.visit_function(element)

db = SQLAlchemy(app)

from sqlalchemy import event
from sqlalchemy.engine import Engine

@event.listens_for(Engine, "connect")
def sqlite_engine_connect(dbapi_connection, connection_record):
    if type(dbapi_connection).__name__ == "Connection":
        def custom_to_char(date_val, fmt):
            if not date_val: return None
            date_str = str(date_val)
            if fmt == 'YYYY-MM':
                return date_str[:7]
            elif fmt == 'YYYY-MM-DD':
                return date_str[:10]
            return date_str
        try:
            dbapi_connection.create_function("to_char", 2, custom_to_char)
        except Exception:
            pass

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# --- Constants ---
FACTORY_LAT = 30.823135
FACTORY_LNG = 31.009878
ALLOWED_RADIUS = 30  # meters
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
SEASON_START = datetime(2025, 1, 1)
SEASON_END = datetime(2026, 7, 15)

def cairo_now():
    cairo_tz = pytz.timezone('Africa/Cairo')
    return datetime.now(cairo_tz).replace(tzinfo=None)

def ensure_rls_on_all_tables():
    """تشغيل RLS على أي جدول في public مقفول فيه (زي الجداول الجديدة اللي db.create_all بيعملها).
    من غير صلاحيات (policies)، فمحدش يقدر يدخل الجداول من الـ Data API بتاع Supabase،
    والسيستم مش بيتأثر لأنه بيدخل بمستخدم postgres (صاحب الجداول وبيعدّي RLS).
    Postgres بس، وأي خطأ بيتطبع ومش بيوقّف السيستم."""
    try:
        if db.engine.dialect.name != 'postgresql':
            return
        from sqlalchemy import text
        with db.engine.begin() as conn:
            tables = conn.execute(text(
                "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind = 'r' AND NOT c.relrowsecurity"
            )).scalars().all()
            for t in tables:
                conn.execute(text('ALTER TABLE public."%s" ENABLE ROW LEVEL SECURITY' % t.replace('"', '""')))
        if tables:
            print(f"[SECURITY] Enabled RLS on: {', '.join(tables)}")
    except Exception as e:
        print(f"[SECURITY] Could not enable RLS automatically: {e}")

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS
