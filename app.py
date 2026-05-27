from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import os
from datetime import datetime, timedelta
from collections import defaultdict

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-me')

if os.environ.get('DATABASE_URL'):
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ['DATABASE_URL']
    app.config['SQLALCHEMY_DATABASE_URI'] = app.config['SQLALCHEMY_DATABASE_URI'].replace("postgres://", "postgresql://", 1)
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///finance.db'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# ================= МОДЕЛИ =================

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    transactions = db.relationship('Transaction', backref='user', lazy=True)
    credits = db.relationship('Credit', backref='user', lazy=True)
    deposits = db.relationship('Deposit', backref='user', lazy=True)
    debts_owed = db.relationship('DebtOwed', backref='user', lazy=True)
    subscriptions = db.relationship('Subscription', backref='user', lazy=True)

class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    amount = db.Column(db.Float, nullable=False)
    category = db.Column(db.String(50), nullable=False)
    type = db.Column(db.String(10), nullable=False)
    date = db.Column(db.DateTime, default=db.func.current_timestamp())
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

class Credit(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    total_amount = db.Column(db.Float, nullable=False)
    interest_rate = db.Column(db.Float, nullable=False)
    monthly_payment_fixed = db.Column(db.Float, nullable=False)
    payment_frequency = db.Column(db.String(20), default="monthly")
    payment_day = db.Column(db.Integer, default=1)
    start_month = db.Column(db.Integer, default=datetime.utcnow().month)
    start_year = db.Column(db.Integer, default=datetime.utcnow().year)
    start_date = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    payments = db.relationship('CreditPayment', backref='credit', lazy=True, order_by="CreditPayment.due_date.asc()")

    @property
    def total_with_interest(self):
        if self.interest_rate > 0:
            return self.total_amount * (1 + self.interest_rate / 100)
        return self.total_amount

    @property
    def total_paid(self):
        return sum(p.amount_paid for p in self.payments if p.is_paid)

    @property
    def remaining_debt(self):
        return max(0, self.total_with_interest - self.total_paid)

class CreditPayment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    credit_id = db.Column(db.Integer, db.ForeignKey('credit.id'), nullable=False)
    due_date = db.Column(db.DateTime, nullable=False)
    amount_due = db.Column(db.Float, nullable=False)
    amount_paid = db.Column(db.Float, default=0.0)
    is_paid = db.Column(db.Boolean, default=False)
    note = db.Column(db.String(200))

class Deposit(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    bank_name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(100), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    interest_rate = db.Column(db.Float, nullable=False)
    term_months = db.Column(db.Integer, nullable=False)
    date_created = db.Column(db.DateTime, default=db.func.current_timestamp())
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    @property
    def total_profit(self): return self.amount * (self.interest_rate / 100) * (self.term_months / 12)
    @property
    def total_amount_end(self): return self.amount + self.total_profit

class DebtOwed(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    debtor_name = db.Column(db.String(100), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    description = db.Column(db.String(200))
    is_paid = db.Column(db.Boolean, default=False)
    date_created = db.Column(db.DateTime, default=db.func.current_timestamp())
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

class Subscription(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    service_name = db.Column(db.String(100), nullable=False)
    plan_name = db.Column(db.String(100), nullable=False)
    cost = db.Column(db.Float, nullable=False)
    billing_cycle = db.Column(db.String(20), default="monthly")
    is_active = db.Column(db.Boolean, default=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

# ================= ДЕКОРАТОРЫ =================
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Пожалуйста, войдите в систему.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# ================= ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =================

def add_months(source_date, months):
    month = source_date.month - 1 + months
    year = source_date.year + month // 12
    month = month % 12 + 1
    day = min(source_date.day, [31,29 if year%4==0 and not year%100==0 or year%400==0 else 28,31,30,31,30,31,31,30,31,30,31][month-1])
    return source_date.replace(year=year, month=month, day=day)

def add_weeks(source_date, weeks):
    return source_date + timedelta(weeks=weeks)

def generate_payment_schedule(credit):
    for p in credit.payments:
        if not p.is_paid:
            db.session.delete(p)
    db.session.flush()
    
    total_with_interest = credit.total_with_interest
    remaining_balance = total_with_interest - credit.total_paid
    
    if remaining_balance <= 0:
        return
    
    try:
        start_date = datetime(credit.start_year, credit.start_month, 1)
    except:
        start_date = datetime.utcnow()
    
    current_date = start_date
    payment_count = 0
    max_payments = 240
    
    while remaining_balance > 0 and payment_count < max_payments:
        payment_amount = min(credit.monthly_payment_fixed, remaining_balance)
        
        payment_record = CreditPayment(
            credit_id=credit.id,
            due_date=current_date,
            amount_due=payment_amount,
            amount_paid=0.0,
            is_paid=False
        )
        db.session.add(payment_record)
        
        remaining_balance -= payment_amount
        
        if credit.payment_frequency == "biweekly":
            current_date = add_weeks(current_date, 2)
        else:
            current_date = add_months(current_date, 1)
        
        payment_count += 1

def get_chart_data(user_id):
    """Собирает данные для графиков"""
    expenses_by_category = defaultdict(float)
    transactions = Transaction.query.filter_by(user_id=user_id, type='expense').all()
    
    for t in transactions:
        expenses_by_category[t.category] += t.amount
    
    months_data = []
    today = datetime.utcnow()
    
    for i in range(5, -1, -1):
        month_start = today.replace(day=1) - timedelta(days=30*i)
        month_end = month_start + timedelta(days=31)
        
        month_income = sum(t.amount for t in Transaction.query.filter(
            Transaction.user_id == user_id,
            Transaction.type == 'income',
            Transaction.date >= month_start,
            Transaction.date < month_end
        ).all())
        
        month_expense = sum(t.amount for t in Transaction.query.filter(
            Transaction.user_id == user_id,
            Transaction.type == 'expense',
            Transaction.date >= month_start,
            Transaction.date < month_end
        ).all())
        
        months_data.append({
            'month': month_start.strftime('%b'),
            'income': month_income,
            'expense': month_expense
        })
    
    return {
        'categories': list(expenses_by_category.keys()),
        'category_amounts': list(expenses_by_category.values()),
        'months_data': months_data
    }

# ================= МАРШРУТЫ =================

@app.route('/')
def home(): return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username, password = request.form.get('username'), request.form.get('password')
        if User.query.filter_by(username=username).first():
            flash('Логин занят.', 'danger')
            return redirect(url_for('register'))
        db.session.add(User(username=username, password_hash=generate_password_hash(password)))
        db.session.commit()
        flash('Регистрация успешна!', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form.get('username')).first()
        if user and check_password_hash(user.password_hash, request.form.get('password')):
            session['user_id'], session['username'] = user.id, user.username
            return redirect(url_for('dashboard'))
        flash('Неверный логин или пароль.', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/dashboard', methods=['GET', 'POST'])
@login_required
def dashboard():
    user_id = session['user_id']

    # 1. Транзакции
    if request.method == 'POST' and 'amount' in request.form and 'credit_name' not in request.form and 'bank_name' not in request.form and 'debtor_name' not in request.form and 'service_name' not in request.form and 'payment_id' not in request.form and 'payment_day' not in request.form:
        try:
            amount, category, trans_type = float(request.form.get('amount')), request.form.get('category'), request.form.get('type')
            if amount <= 0: raise ValueError("Сумма > 0")
            db.session.add(Transaction(amount=amount, category=category, type=trans_type, user_id=user_id))
            db.session.commit()
            flash('Операция добавлена.', 'success')
        except Exception as e: flash('Ошибка: ' + str(e), 'danger')
        return redirect(url_for('dashboard'))

    # 2. Создание КРЕДИТА
    if request.method == 'POST' and 'credit_name' in request.form:
        try:
            name = request.form.get('credit_name')
            total = float(request.form.get('total_amount'))
            rate = float(request.form.get('interest_rate'))
            fixed_payment = float(request.form.get('monthly_payment_fixed'))
            frequency = request.form.get('payment_frequency', 'monthly')
            payment_day = int(request.form.get('payment_day', 1))
            start_month = int(request.form.get('start_month', datetime.utcnow().month))
            start_year = int(request.form.get('start_year', datetime.utcnow().year))
            
            new_credit = Credit(
                name=name, total_amount=total, interest_rate=rate, monthly_payment_fixed=fixed_payment,
                payment_frequency=frequency, payment_day=payment_day, start_month=start_month, start_year=start_year, user_id=user_id
            )
            db.session.add(new_credit)
            db.session.flush()
            generate_payment_schedule(new_credit)
            db.session.commit()
            flash('Кредит и график платежей созданы!', 'success')
        except Exception as e: 
            db.session.rollback()
            flash('Ошибка: ' + str(e), 'danger')
        return redirect(url_for('dashboard'))

    # 3. Внесение ПЛАТЕЖА по кредиту (ИСПРАВЛЕНО: категория теперь содержит название кредита)
    if request.method == 'POST' and 'payment_id' in request.form:
        try:
            pay_id = int(request.form.get('payment_id'))
            paid_amount = float(request.form.get('paid_amount'))
            note = request.form.get('note', '')
            
            payment = CreditPayment.query.get_or_404(pay_id)
            if payment.credit.user_id == user_id:
                payment.amount_paid = paid_amount
                payment.is_paid = True
                payment.note = note
                
                # Создаём транзакцию с понятной категорией: "Кредит: [Название]"
                credit_name = payment.credit.name if payment.credit.name else "Без названия"
                trans = Transaction(
                    amount=paid_amount, 
                    category=f"Кредит: {credit_name}", 
                    type='expense', 
                    user_id=user_id
                )
                db.session.add(trans)
                
                generate_payment_schedule(payment.credit)
                db.session.commit()
                flash('Платеж внесен! График пересчитан.', 'success')
        except Exception as e:
            db.session.rollback()
            flash('Ошибка: ' + str(e), 'danger')
        return redirect(url_for('dashboard'))

    # 4. Вклады
    if request.method == 'POST' and 'bank_name' in request.form:
        try:
            bank, desc = request.form.get('bank_name'), request.form.get('description')
            amount, rate, term = float(request.form.get('amount')), float(request.form.get('interest_rate')), int(request.form.get('term_months'))
            db.session.add(Deposit(bank_name=bank, description=desc, amount=amount, interest_rate=rate, term_months=term, user_id=user_id))
            db.session.commit()
            flash('Вклад открыт!', 'success')
        except Exception as e: flash('Ошибка: ' + str(e), 'danger')
        return redirect(url_for('dashboard'))

    # 5. Долги
    if request.method == 'POST' and 'debtor_name' in request.form:
        try:
            debtor, amount, desc = request.form.get('debtor_name'), float(request.form.get('amount')), request.form.get('description')
            db.session.add(DebtOwed(debtor_name=debtor, amount=amount, description=desc, user_id=user_id))
            db.session.commit()
            flash('Долг записан!', 'success')
        except Exception as e: flash('Ошибка: ' + str(e), 'danger')
        return redirect(url_for('dashboard'))

    # 6. Подписки
    if request.method == 'POST' and 'service_name' in request.form:
        try:
            service, plan = request.form.get('service_name'), request.form.get('plan_name')
            cost, cycle = float(request.form.get('cost')), request.form.get('billing_cycle')
            db.session.add(Subscription(service_name=service, plan_name=plan, cost=cost, billing_cycle=cycle, user_id=user_id))
            db.session.commit()
            flash('Подписка добавлена!', 'success')
        except Exception as e: flash('Ошибка: ' + str(e), 'danger')
        return redirect(url_for('dashboard'))

    # Сбор данных
    transactions = Transaction.query.filter_by(user_id=user_id).order_by(Transaction.date.desc()).all()
    credits = Credit.query.filter_by(user_id=user_id).all()
    deposits = Deposit.query.filter_by(user_id=user_id).all()
    debts = DebtOwed.query.filter_by(user_id=user_id).all()
    subscriptions = Subscription.query.filter_by(user_id=user_id, is_active=True).all()

    income = sum(t.amount for t in transactions if t.type == 'income')
    expense = sum(t.amount for t in transactions if t.type == 'expense')
    subscriptions_total = sum(s.cost for s in subscriptions)
    total_expenses = expense + subscriptions_total
    balance = income - total_expenses
    
    today = datetime.utcnow()
    one_month_later = add_months(today, 1)
    upcoming_payments = 0
    for c in credits:
        for p in c.payments:
            if not p.is_paid and p.due_date >= today and p.due_date <= one_month_later:
                upcoming_payments += p.amount_due

    total_debts_owed = sum(d.amount for d in debts if not d.is_paid)
    
    chart_data = get_chart_data(user_id)

    return render_template('dashboard.html', 
                           transactions=transactions, balance=balance, income=income, expense=total_expenses,
                           credits=credits, deposits=deposits, debts=debts, subscriptions=subscriptions,
                           upcoming_payments=upcoming_payments, total_subscriptions=subscriptions_total,
                           total_debts_owed=total_debts_owed, now=datetime.utcnow(), chart_data=chart_data)

# Удаления
@app.route('/delete_trans/<int:id>')
@login_required
def delete_transaction(id):
    t = Transaction.query.get_or_404(id)
    if t.user_id == session['user_id']: db.session.delete(t); db.session.commit()
    return redirect(url_for('dashboard'))

@app.route('/delete_credit/<int:id>')
@login_required
def delete_credit(id):
    c = Credit.query.get_or_404(id)
    if c.user_id == session['user_id']: 
        for p in c.payments: db.session.delete(p)
        db.session.delete(c)
        db.session.commit()
    return redirect(url_for('dashboard'))

@app.route('/delete_deposit/<int:id>')
@login_required
def delete_deposit(id):
    d = Deposit.query.get_or_404(id)
    if d.user_id == session['user_id']: db.session.delete(d); db.session.commit()
    return redirect(url_for('dashboard'))

@app.route('/delete_debt/<int:id>')
@login_required
def delete_debt(id):
    d = DebtOwed.query.get_or_404(id)
    if d.user_id == session['user_id']: db.session.delete(d); db.session.commit()
    return redirect(url_for('dashboard'))

@app.route('/delete_sub/<int:id>')
@login_required
def delete_subscription(id):
    s = Subscription.query.get_or_404(id)
    if s.user_id == session['user_id']: db.session.delete(s); db.session.commit()
    return redirect(url_for('dashboard'))

with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=True)