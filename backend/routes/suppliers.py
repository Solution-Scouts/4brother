"""
Routes: suppliers
Auto-extracted from backend/app.py during refactoring.
"""
import os
import json
import math
import re
import random
import string
from datetime import datetime, date, timedelta
from functools import wraps
from flask import render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from sqlalchemy import func, text, or_

from ..core import app, db, cairo_now, basedir, FACTORY_LAT, FACTORY_LNG, ALLOWED_RADIUS, allowed_file, BASE_DIR
from ..models import (
    SystemSetting, AttendanceSettings, User, Attendance, PatternTracking,
    Category, ProductModel, EmployeeExcuse, ProductVariant, Supplier,
    PartnerTransaction, SupplierPayment, PurchaseOrder, PurchaseItem,
    Customer, CustomerPayment, ShippingCompany, SaleOrder, SaleItem,
    FinancialTransaction, ReturnInvoice, MoneyAccount, StockMovement,
    HRTransaction, ExpenseCategory, Expense, SupplierReturn, SupplierReturnItem
)
from ..helpers import (general_manager_required, permission_required, permission_required_any,
                       get_accessible_users, get_allowed_customers,
                       calculate_user_commission, calculate_distance)


@app.route('/suppliers')
@permission_required('manage_inventory')
def suppliers():
    all_suppliers = Supplier.query.filter_by(is_office=False).all()
    total_debt = sum(s.balance for s in all_suppliers if s.balance > 0)
    return render_template('suppliers.html', suppliers=all_suppliers, total_debt=total_debt, is_office=False)

@app.route('/office_suppliers')
@permission_required('manage_inventory')
def office_suppliers():
    all_suppliers = Supplier.query.filter_by(is_office=True).all()
    total_debt = sum(s.balance for s in all_suppliers if s.balance > 0)
    return render_template('suppliers.html', suppliers=all_suppliers, total_debt=total_debt, is_office=True)

@app.route('/suppliers/add', methods=['POST'])
@permission_required('manage_inventory')
def add_supplier():
    name = request.form.get('name')
    phone = request.form.get('phone')
    is_office = request.form.get('is_office') == 'True'
    if name:
        db.session.add(Supplier(name=name, phone=phone, is_office=is_office))
        db.session.commit()
        flash('تم إضافة المورد بنجاح ✅', 'success')
    else: flash('الاسم مطلوب', 'warning')
    return redirect(url_for('office_suppliers' if is_office else 'suppliers'))


@app.route('/supplier/edit/<int:id>', methods=['POST'])
@permission_required('manage_inventory')
def edit_supplier(id):
    supp = Supplier.query.get_or_404(id)
    supp.name = request.form.get('name')
    supp.phone = request.form.get('phone')
    db.session.commit()
    flash('تم التحديث', 'success')
    return redirect(url_for('office_suppliers' if supp.is_office else 'suppliers'))


@app.route('/suppliers/<int:id>')
@permission_required('manage_inventory')
def supplier_profile(id):
    from flask import session
    current_season = session.get('season', 'شتوي 2027')
    
    supp = Supplier.query.get_or_404(id)
    accounts = MoneyAccount.get_ordered()

    # === الإضافة الجديدة: حساب إجمالي عدد القطع للموسم الحالي ===
    total_items = db.session.query(func.sum(PurchaseItem.quantity))\
        .join(PurchaseOrder)\
        .filter(PurchaseOrder.supplier_id == id, PurchaseOrder.season == current_season)\
        .scalar() or 0

    supplier_returns, check = _supplier_returns_and_check(supp)

    return render_template('supplier_profile.html',
                           supplier=supp,
                           orders=sorted(supp.orders, key=lambda o: o.id, reverse=True),
                           payments=sorted(supp.payments, key=lambda p: p.id, reverse=True),
                           accounts=accounts,
                           total_items=int(total_items),
                           supplier_returns=supplier_returns,
                           balance_check=check)


def _supplier_returns_and_check(supp):
    """
    مرتجعات المورد (كل المواسم):
    - الجديدة: من جدول supplier_return بقيمتها الحقيقية.
    - القديمة: كانت بتتسجل كحركات مخزون بس من غير قيمة، فقيمتها بتتحسب تقديرياً
      بتكلفة الصنف من آخر فاتورة شراء قبل تاريخ المرتجع (وده نفس السعر اللي الفورمة كانت بتملاه).
    + مطابقة الرصيد: الفواتير - المدفوعات - المرتجعات.
    الاستعلامات هنا SQL مباشر علشان متتأثرش بفلتر الموسم.
    """
    from sqlalchemy import text as sql
    returns = []

    # 1) المرتجعات الجديدة
    for r in SupplierReturn.query.filter_by(supplier_id=supp.id).all():
        returns.append({
            'id': r.id, 'date': r.date, 'user': r.user.fullname if r.user else '-',
            'qty': r.total_qty or 0, 'value': r.total_value or 0, 'estimated': False,
            'items': [{'name': (it.variant.model.name if it.variant and it.variant.model else f"#{it.variant_id}"),
                       'barcode': it.variant.barcode if it.variant else '',
                       'qty': it.quantity, 'cost': it.unit_cost} for it in r.items],
        })

    # 2) المرتجعات القديمة (حركات مخزون بالصيغة القديمة)
    rows = db.session.execute(sql("""
        SELECT sm.id, sm.timestamp, -sm.quantity_change AS qty, sm.variant_id, u.fullname,
               pm.name AS pname, pv.barcode,
               COALESCE(
                 (SELECT pi.unit_cost FROM purchase_item pi
                    JOIN purchase_order po ON po.id = pi.purchase_id
                   WHERE pi.variant_id = sm.variant_id AND po.date <= sm.timestamp
                   ORDER BY po.date DESC LIMIT 1),
                 pv.cost_price, 0) AS est_cost
          FROM stock_movement sm
          LEFT JOIN product_variant pv ON pv.id = sm.variant_id
          LEFT JOIN product_model pm ON pm.id = pv.model_id
          LEFT JOIN "user" u ON u.id = sm.user_id
         WHERE sm.reason = :reason
         ORDER BY sm.timestamp
    """), {'reason': f"مرتجع شراء للمورد: {supp.name}"}).fetchall()

    group = None
    for row in rows:
        ts = row.timestamp
        if isinstance(ts, str):
            from datetime import datetime as _dt
            ts = _dt.fromisoformat(ts)
        # الأصناف اللي اتسجلت في نفس العملية بيبقى وقتها نفس الثانية تقريباً
        if group is None or (ts - group['date']).total_seconds() > 5:
            group = {'id': None, 'date': ts, 'user': row.fullname or '-', 'qty': 0, 'value': 0.0,
                     'estimated': True, 'items': []}
            returns.append(group)
        cost = float(row.est_cost or 0)
        group['items'].append({'name': row.pname or f"#{row.variant_id}", 'barcode': row.barcode or '',
                               'qty': row.qty, 'cost': cost})
        group['qty'] += row.qty
        group['value'] += row.qty * cost

    # ترقيم المرتجعات بالترتيب الزمني (1 = أقدم مرتجع) علشان المراجعة مع المورد واحد واحد
    returns.sort(key=lambda r: r['date'] or datetime.min)
    for n, r in enumerate(returns, 1):
        r['num'] = n
    returns.reverse()

    # 3) مطابقة الرصيد (كل المواسم)
    purchases = db.session.execute(sql("SELECT COALESCE(SUM(total_cost),0) FROM purchase_order WHERE supplier_id=:s"), {'s': supp.id}).scalar() or 0
    payments = db.session.execute(sql("SELECT COALESCE(SUM(amount),0) FROM supplier_payment WHERE supplier_id=:s"), {'s': supp.id}).scalar() or 0
    returns_total = sum(r['value'] for r in returns)
    expected = float(purchases) - float(payments) - returns_total
    check = {
        'purchases': float(purchases), 'payments': float(payments), 'returns': returns_total,
        'expected': expected, 'actual': float(supp.balance or 0),
        'diff': float(supp.balance or 0) - expected,
        'has_estimated': any(r['estimated'] for r in returns),
    }
    return returns, check

@app.route('/suppliers/pay', methods=['POST'])
@permission_required('manage_inventory')
def add_supplier_payment():
    sid = request.form.get('supplier_id')
    try:
        amount = float(request.form.get('amount') or 0)
    except:
        amount = 0

    acc_id = request.form.get('account_id') # استقبال رقم الخزينة

    if amount <= 0:
        flash('يجب إدخال مبلغ صحيح', 'warning')
        return redirect(url_for('supplier_profile', id=sid))

    # التحقق من الخزينة
    account = MoneyAccount.query.get(acc_id)
    if not account:
        flash('يجب اختيار خزينة للسداد منها', 'danger')
        return redirect(url_for('supplier_profile', id=sid))

    # معالجة الصورة
    fname = None
    if 'receipt_image' in request.files and request.files['receipt_image'].filename:
        fname = f"{int(cairo_now().timestamp())}_{secure_filename(request.files['receipt_image'].filename)}"
        from backend.supabase_storage import upload_file_to_supabase
        success, url = upload_file_to_supabase(request.files['receipt_image'], fname, app.config)
        if success:
            fname = url

    # 1. تسجيل عملية السداد للمورد
    payment = SupplierPayment(
        supplier_id=sid,
        amount=amount,
        receipt_image=fname,
        notes=request.form.get('notes'),
        account_id=account.id # ربطها بالخزنة
    )
    db.session.add(payment)

    # 2. تقليل مديونية المورد
    supplier = Supplier.query.get(sid)
    supplier.balance -= amount

    # 3. خصم المبلغ من الخزينة المختارة وتسجيل حركة مالية
    account.balance = round(account.balance - amount, 1)

    db.session.add(FinancialTransaction(
        type='expense', # مصروف
        category='سداد موردين',
        amount=-amount, # بالسالب
        description=f"سداد دفعة للمورد ({supplier.name})",
        date=cairo_now(),
        created_by_id=current_user.id,
        account_id=account.id
    ))

    db.session.commit()
    flash(f'تم تسجيل السداد وخصم {amount} من {account.name} ✅', 'success')
    return redirect(url_for('supplier_profile', id=sid))

@app.route('/suppliers/<int:id>/edit_balance', methods=['POST'])
@permission_required('manage_inventory')
def edit_supplier_balance(id):
    supp = Supplier.query.get_or_404(id)
    new_balance = float(request.form.get('new_balance', 0))
    supp.balance = new_balance
    db.session.commit()
    flash(f'تم تعديل رصيد المورد {supp.name} بنجاح إلى {new_balance}', 'success')
    return redirect(url_for('supplier_profile', id=id))

# === تقارير تفصيلية للمدفوعات الموردين ===
