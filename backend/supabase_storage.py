import os
import time
import tempfile
import io
from werkzeug.utils import secure_filename
import cloudinary
import cloudinary.uploader

def compress_image(file_obj, max_width=800, quality=60):
    """
    ضغط الصور بشكل قوي لتقليل حجمها وتوفير مساحة التخزين والباندويث.
    """
    try:
        from PIL import Image
        img = Image.open(file_obj)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        
        if img.width > max_width:
            ratio = max_width / img.width
            new_height = int(img.height * ratio)
            img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)
            
        output = io.BytesIO()
        img.save(output, format="WEBP", quality=quality, optimize=True)
        output.seek(0)
        return output, True
    except Exception as e:
        print(f"Image compression failed: {e}")
        file_obj.seek(0)
        return file_obj, False

def upload_file_to_cloudinary(file_obj, filename, app_config=None):
    """
    رفع الملفات إلى Cloudinary مباشرة مع ضغط تلقائي وتوفير أقصى سرعة تحميل.
    """
    cloud_name = os.environ.get("CLOUDINARY_CLOUD_NAME", "").strip()
    api_key = os.environ.get("CLOUDINARY_API_KEY", "").strip()
    api_secret = os.environ.get("CLOUDINARY_API_SECRET", "").strip()
    if not (cloud_name and api_key and api_secret):
        return False, "بيانات Cloudinary مش متضافة في متغيرات البيئة (CLOUDINARY_CLOUD_NAME / API_KEY / API_SECRET)"

    cloudinary.config(
        cloud_name=cloud_name,
        api_key=api_key,
        api_secret=api_secret,
        secure=True
    )

    try:
        clean_name = f"{int(time.time())}_{secure_filename(filename)}"
        clean_name, _ = os.path.splitext(clean_name)
        
        # قراءة البيانات
        if hasattr(file_obj, 'read'):
            file_bytes = file_obj.read()
        else:
            file_bytes = file_obj
            
        res = cloudinary.uploader.upload(
            file_bytes,
            public_id=f"erp_uploads/{clean_name}",
            resource_type="image",
            overwrite=True,
            format="webp"
        )
        return True, res.get("secure_url")
    except Exception as e:
        return False, f"فشل الرفع على Cloudinary: {str(e)}"

# توجيه دالة الرفع القديمة لـ Cloudinary لتعمل كل أقسام النظام تلقائياً
def upload_file_to_supabase(file_obj, filename, app_config=None):
    return upload_file_to_cloudinary(file_obj, filename, app_config)
