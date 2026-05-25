from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import os
from datetime import datetime, timedelta
import math

# Создание приложения Flask
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-me')

# Настройка базы данных
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

# Обновленная модель Кредита
class Credit(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    total_amount = db.Column(db.Float, nullable=False) # Сумма взятая
    interest_rate = db.Column(db.Float, nullable=False) # Годовая ставка %
    monthly_payment_fixed = db.Column(db.Float, nullable=False) # Желаемый платеж или расчетный
    start_date = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # Связь с историей платежей
    payments = db.relationship('CreditPayment', backref='credit', lazy=True, order_by="CreditPayment.due_date.asc()")

    @property
    def total_paid(self):
        return sum(p.amount_paid for p in self.payments if p.is_paid)

    @property
    def remaining_debt(self):
        # Упрощенный расчет остатка: Сумма - Выплачено
        # В реальности нужно учитывать проценты, но для трекинга "сколько осталось внести" этого достаточно
        return max(0, self.total_amount - self.total_paid)

# Новая модель: История платежей по кредиту
class CreditPayment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    credit_id = db.Column(db.Integer, db.ForeignKey('credit.id'), nullable=False)
    due_date = db.Column(db.DateTime, nullable=False) # Дата платежа
    amount_due = db.Column(db.Float, nullable=False)   # Сколько нужно внести
    amount_paid = db.Column(db.Float, default=0.0)     # Сколько внесено фактически
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
    if request.method == 'POST' and 'amount' in request.form and 'credit_name' not in request.form and 'bank_name' not in request.form and 'debtor_name' not in request.form and 'service_name' not in request.form:
        try:
            amount, category, trans_type = float(request.form.get('amount')), request.form.get('category'), request.form.get('type')
            if amount <= 0: raise ValueError("Сумма > 0")
            db.session.add(Transaction(amount=amount, category=category, type=trans_type, user_id=user_id))
            db.session.commit()
            flash('Операция добавлена.', 'success')
        except Exception as e: flash('Ошибка: ' + str(e), 'danger')
        return redirect(url_for('dashboard'))

    # 2. Создание КРЕДИТА с графиком
    if request.method == 'POST' and 'credit_name' in request.form:
        try:
            name = request.form.get('credit_name')
            total = float(request.form.get('total_amount'))
            rate = float(request.form.get('interest_rate'))
            fixed_payment = float(request.form.get('monthly_payment_fixed'))
            
            # Создаем кредит
            new_credit = Credit(name=name, total_amount=total, interest_rate=rate, monthly_payment_fixed=fixed_payment, user_id=user_id)
            db.session.add(new_credit)
            db.session.flush() # Чтобы получить ID кредита до коммита

            # Генерируем график платежей
            # Рассчитываем примерное количество месяцев
            months_count = math.ceil(total / fixed_payment) if fixed_payment > 0 else 12
            
            current_date = datetime.utcnow()
            remaining_balance = total

            for i in range(months_count):
                # Дата следующего платежа (каждый месяц)
                next_date = current_date.replace(day=current_date.day) + timedelta(days=30*i)
                
                # Если остаток меньше фиксированного платежа, платим остаток
                payment_amount = min(fixed_payment, remaining_balance)
                
                if payment_amount <= 0: break

                payment_record = CreditPayment(
                    credit_id=new_credit.id,
                    due_date=next_date,
                    amount_due=payment_amount,
                    amount_paid=0.0,
                    is_paid=False
                )
                db.session.add(payment_record)
                remaining_balance -= payment_amount

            db.session.commit()
            flash('Кредит и график платежей созданы!', 'success')
        except Exception as e: 
            db.session.rollback()
            flash('Ошибка: ' + str(e), 'danger')
        return redirect(url_for('dashboard'))

    # 3. Внесение ПЛАТЕЖА по кредиту
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
                
                # Также создаем транзакцию расхода автоматически
                trans = Transaction(
                    amount=paid_amount, 
                    category=f"Платеж: {payment.credit.name}", 
                    type='expense', 
                    user_id=user_id
                )
                db.session.add(trans)
                db.session.commit()
                flash('Платеж внесен!', 'success')
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
    balance = income - expense
    
    # Считаем текущую нагрузку (ближайшие платежи)
    today = datetime.utcnow()
    upcoming_payments = 0
    for c in credits:
        for p in c.payments:
            if not p.is_paid and p.due_date >= today and p.due_date < today + timedelta(days=30):
                upcoming_payments += p.amount_due

    total_subscriptions = sum(s.cost for s in subscriptions)
    total_debts_owed = sum(d.amount for d in debts if not d.is_paid)

    return render_template('dashboard.html', 
                           transactions=transactions, balance=balance, income=income, expense=expense,
                           credits=credits, deposits=deposits, debts=debts, subscriptions=subscriptions,
                           upcoming_payments=upcoming_payments,
                           total_subscriptions=total_subscriptions,
                           total_debts_owed=total_debts_owed)

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
        # Удаляем связанные платежи
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

# Инициализация БД
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=True)