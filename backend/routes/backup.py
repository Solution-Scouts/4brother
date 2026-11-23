"""
Routes: Admin Backup Management
Exclusive for Ahmed Abd-Elfattah (General Manager)
Allows on-demand backup generation, direct ZIP downloads, and instant Telegram dispatch.
"""
import os
import glob
import requests
from datetime import datetime, timezone, timedelta
from flask import render_template, request, redirect, url_for, flash, jsonify, send_file
from flask_login import login_required, current_user

from ..core import app, db
from ..backup_service import create_backup_archive, get_base_dir
from ..models import ProductModel, ProductVariant, User

def is_ahmed_abdelfattah():
    """Strict authorization check: only Ahmed Abd-Elfattah can access."""
    if not current_user.is_authenticated:
        return False
    return (
        current_user.username == 'gm_ahmed' or 
        current_user.fullname == 'أحمد عبد الفتاح' or 
        (current_user.role == 'general_manager' and getattr(current_user, 'id', None) == 1)
    )

@app.route('/admin/backup')
@login_required
def admin_backup_dashboard():
    if not is_ahmed_abdelfattah():
        flash("عفواً، هذه الصفحة مخصصة للأستاذ أحمد عبد الفتاح فقط.", "danger")
        return redirect(url_for('dashboard'))

    # Gather quick stats
    base_dir = get_base_dir()
    backups_dir = os.path.join(base_dir, "backups")
    recent_backups = []
    
    if os.path.exists(backups_dir):
        zip_files = glob.glob(os.path.join(backups_dir, "*.zip"))
        zip_files.sort(key=os.path.getmtime, reverse=True)
        for f in zip_files[:10]:
            try:
                mtime = datetime.fromtimestamp(os.path.getmtime(f), tz=timezone(timedelta(hours=2)))
                size_mb = round(os.path.getsize(f) / (1024 * 1024), 2)
                recent_backups.append({
                    "filename": os.path.basename(f),
                    "size_mb": size_mb,
                    "date": mtime.strftime("%Y-%m-%d %I:%M %p")
                })
            except Exception:
                pass

    total_models = ProductModel.query.count()
    total_variants = ProductVariant.query.count()
    total_users = User.query.count()

    return render_template(
        'backup.html',
        recent_backups=recent_backups,
        total_models=total_models,
        total_variants=total_variants,
        total_users=total_users,
        user=current_user
    )

@app.route('/admin/backup/download')
@login_required
def admin_backup_download():
    if not is_ahmed_abdelfattah():
        flash("غير مصرح لك بتحميل النسخة الاحتياطية.", "danger")
        return redirect(url_for('dashboard'))

    backup_type = request.args.get('type', 'db')  # 'db' (fast) or 'full' (with images)
    specific_file = request.args.get('file')

    # If downloading an existing backup from list
    if specific_file:
        safe_name = os.path.basename(specific_file)
        base_dir = get_base_dir()
        file_path = os.path.join(base_dir, "backups", safe_name)
        if os.path.exists(file_path):
            return send_file(
                file_path,
                as_attachment=True,
                download_name=safe_name,
                mimetype='application/zip'
            )

    # Generate fresh backup on demand
    include_images = (backup_type == 'full')
    try:
        res = create_backup_archive(include_images=include_images)
        if res.get('success') and os.path.exists(res.get('local_path', '')):
            return send_file(
                res['local_path'],
                as_attachment=True,
                download_name=res['zip_name'],
                mimetype='application/zip'
            )
        else:
            flash(f"فشل إعداد النسخة الاحتياطية: {res}", "danger")
            return redirect(url_for('admin_backup_dashboard'))
    except Exception as e:
        flash(f"حدث خطأ أثناء تجهيز النسخة: {str(e)}", "danger")
        return redirect(url_for('admin_backup_dashboard'))

@app.route('/admin/backup/send-telegram', methods=['POST'])
@login_required
def admin_backup_telegram():
    if not is_ahmed_abdelfattah():
        flash("غير مصرح لك بتنفيذ هذه العملية.", "danger")
        return redirect(url_for('dashboard'))

    try:
        from run_daily_backup import send_telegram_alert
        # Create full backup
        res = create_backup_archive(include_images=True)
        if res.get('success'):
            cairo_now = datetime.now(timezone(timedelta(hours=2)))
            img_stats = res['manifest'].get('image_stats', {})
            total_images = img_stats.get('downloaded', 0) + img_stats.get('already_cached', 0)
            
            tg_msg = (
                f"⚡ <b>نسخة احتياطية يدوية فورية (طلب الأستاذ أحمد عبد الفتاح)</b>\n"
                f"📅 التاريخ: {cairo_now.strftime('%Y-%m-%d %I:%M %p')} (توقيت مصر)\n"
                f"📦 الملف: <code>{res['zip_name']}</code>\n"
                f"💾 الحجم: {res['size_mb']} MB\n"
                f"📊 الجداول: {res['manifest'].get('total_tables', 27)} جدولاً\n"
                f"🖼️ صور الموديلات: {total_images} صورة"
            )
            send_telegram_alert(tg_msg, res['local_path'])
            flash("تم توليد النسخة الاحتياطية الشاملة وإرسالها فوراً إلى التيليجرام بنجاح! 🚀✅", "success")
        else:
            flash(f"فشل إنشاء النسخة الاحتياطية: {res}", "danger")
    except Exception as e:
        flash(f"حدث خطأ أثناء الإرسال للتيليجرام: {str(e)}", "danger")

    return redirect(url_for('admin_backup_dashboard'))

# التوكن ورقم الشات بيتقروا من متغيرات البيئة TELEGRAM_BOT_TOKEN و TELEGRAM_CHAT_ID
DEFAULT_BOT_TOKEN = ""
DEFAULT_CHAT_ID = ""

def get_telegram_config():
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or DEFAULT_BOT_TOKEN).strip().strip("\ufeff")
    chat_id = (os.environ.get("TELEGRAM_CHAT_ID") or DEFAULT_CHAT_ID).strip().strip("\ufeff")
    return token, chat_id

@app.route('/admin/backup/setup-telegram-webhook')
@login_required
def setup_telegram_webhook():
    if not is_ahmed_abdelfattah():
        flash("غير مصرح لك بتنفيذ هذه العملية.", "danger")
        return redirect(url_for('dashboard'))

    token, _ = get_telegram_config()
    host_url = request.host_url.rstrip('/')
    if not host_url.startswith("http://localhost") and not host_url.startswith("http://127.0.0.1"):
        host_url = host_url.replace("http://", "https://", 1)

    webhook_url = f"{host_url}/api/telegram/webhook"
    
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{token}/setWebhook",
            params={"url": webhook_url},
            timeout=10
        )
        res_data = r.json()
        if res_data.get("ok"):
            flash(f"تم ربط استقبال رسائل التيليجرام بنجاح! الرابط: {webhook_url} 🎉", "success")
        else:
            flash(f"فشل ربط التيليجرام: {res_data.get('description')}", "danger")
    except Exception as e:
        flash(f"خطأ أثناء الاتصال بتيليجرام: {str(e)}", "danger")

    return redirect(url_for('admin_backup_dashboard'))

@app.route('/api/telegram/webhook', methods=['POST'])
def telegram_webhook():
    """
    Telegram Bot Webhook: Allows Ahmed Abd-Elfattah to message the bot
    and instantly receive a fresh database backup directly in chat!
    """
    try:
        data = request.get_json(force=True, silent=True) or {}
        message = data.get('message') or data.get('edited_message') or {}
        chat = message.get('chat', {})
        chat_id = str(chat.get('id', ''))
        text = (message.get('text') or '').strip()

        token, allowed_chat_id = get_telegram_config()

        if not chat_id:
            return jsonify({"ok": True})

        # Security check: only authorized Chat ID (Ahmed Abd-Elfattah)
        if chat_id != allowed_chat_id:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": "⛔ عفواً، هذا البوت مخصص للمدير العام فقط وغير مصرح لك بالاستخدام."}
            )
            return jsonify({"ok": True})

        text_lower = text.lower()

        # Help / Start command
        if text_lower in ['/start', '/help', 'مساعدة', 'اوامر', 'start']:
            help_msg = (
                "👋 <b>أهلاً بك يا أستاذ أحمد في بوت النسخ الاحتياطي!</b>\n\n"
                "يمكنك إرسال أي من الأوامر التالية في أي وقت وسيرد عليك البوت فوراً:\n\n"
                "📦 <code>/db</code> أو <b>باك اب</b> : لسحب قاعدة البيانات الحالية وإرسالها فوراً.\n"
                "🖼️ <code>/full</code> : لطلب نسخة شاملة مع صور الموديلات.\n"
                "📊 <code>/status</code> : لفحص حالة اتصال السيرفر."
            )
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={
                "chat_id": chat_id, "text": help_msg, "parse_mode": "HTML"
            })
            return jsonify({"ok": True})

        # Status command
        elif text_lower in ['/status', 'حالة', 'status']:
            cairo_now = datetime.now(timezone(timedelta(hours=2)))
            status_msg = (
                f"🟢 <b>سيرفر النسخ الاحتياطي متصل ويعمل 24/7!</b>\n"
                f"📅 التوقيت الآن: {cairo_now.strftime('%Y-%m-%d %I:%M %p')} (مصر)\n"
                f"🏢 النظام: 4Brother ERP\n"
                f"✅ يمكنك طلب نسخة قاعدة البيانات في أي وقت بإرسال كلمة <b>باك اب</b>."
            )
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={
                "chat_id": chat_id, "text": status_msg, "parse_mode": "HTML"
            })
            return jsonify({"ok": True})

        # Database on-demand command (Fast & lightweight ~3-5 seconds)
        elif any(k in text_lower for k in ['/db', '/backup', 'backup', 'باك اب', 'داتابيز', 'قاعدة البيانات', 'داتا']):
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={
                "chat_id": chat_id,
                "text": "⏳ <b>جاري سحب وتجهيز قاعدة البيانات الآن... ثوانٍ معدودة وتصلك.</b>",
                "parse_mode": "HTML"
            })

            res = create_backup_archive(include_images=False)
            if res.get('success') and os.path.exists(res.get('local_path', '')):
                cairo_now = datetime.now(timezone(timedelta(hours=2)))
                caption = (
                    f"📦 <b>نسخة قاعدة البيانات (طلب فوري)</b>\n"
                    f"📅 التاريخ: {cairo_now.strftime('%Y-%m-%d %I:%M %p')}\n"
                    f"💾 الحجم: {res['size_mb']} MB\n"
                    f"📊 الجداول: {res['manifest'].get('total_tables', 27)} جدولاً"
                )
                with open(res['local_path'], 'rb') as f:
                    requests.post(
                        f"https://api.telegram.org/bot{token}/sendDocument",
                        data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"},
                        files={"document": f},
                        timeout=90
                    )
            else:
                requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={
                    "chat_id": chat_id,
                    "text": f"❌ حدث خطأ أثناء تجهيز النسخة: {res}",
                    "parse_mode": "HTML"
                })
            return jsonify({"ok": True})

        # Full backup with images
        elif any(k in text_lower for k in ['/full', 'شامل', 'كامل', 'صور']):
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={
                "chat_id": chat_id,
                "text": "⏳ <b>جاري تجهيز النسخة الشاملة (قاعدة البيانات + صور الموديلات)... قد تستغرق دقيقة.</b>",
                "parse_mode": "HTML"
            })
            res = create_backup_archive(include_images=True)
            if res.get('success') and os.path.exists(res.get('local_path', '')):
                cairo_now = datetime.now(timezone(timedelta(hours=2)))
                caption = (
                    f"📦 <b>نسخة شاملة (قاعدة بيانات + صور)</b>\n"
                    f"📅 التاريخ: {cairo_now.strftime('%Y-%m-%d %I:%M %p')}\n"
                    f"💾 الحجم: {res['size_mb']} MB"
                )
                with open(res['local_path'], 'rb') as f:
                    requests.post(
                        f"https://api.telegram.org/bot{token}/sendDocument",
                        data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"},
                        files={"document": f},
                        timeout=120
                    )
            return jsonify({"ok": True})

        # Default fallback
        else:
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={
                "chat_id": chat_id,
                "text": "💡 أهلاً بك يا أستاذ أحمد! أرسل كلمة <b>باك اب</b> أو <code>/db</code> لسحب قاعدة البيانات فوراً وإرسالها لك كملف ZIP.",
                "parse_mode": "HTML"
            })
            return jsonify({"ok": True})

    except Exception as e:
        print(f"[TELEGRAM WEBHOOK ERROR] {e}")
        return jsonify({"ok": False, "error": str(e)}), 500
