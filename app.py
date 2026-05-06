from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import os

# Создание приложения Flask
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-me')

# Настройка базы данных (SQLite локально, PostgreSQL на Render)
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
    type = db.Column(db.String(10), nullable=False) # income / expense
    date = db.Column(db.DateTime, default=db.func.current_timestamp())
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

class Credit(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    credit_type = db.Column(db.String(50), default="installment") # mortgage, installment, loan
    total_amount = db.Column(db.Float, nullable=False)
    monthly_payment = db.Column(db.Float, nullable=False)
    amount_paid = db.Column(db.Float, default=0.0)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    @property
    def remaining_amount(self): return self.total_amount - self.amount_paid
    @property
    def months_left(self):
        return round(self.remaining_amount / self.monthly_payment, 1) if self.monthly_payment > 0 else 0

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
    service_name = db.Column(db.String(100), nullable=False) # Netflix, Yandex, etc.
    plan_name = db.Column(db.String(100), nullable=False)    # Premium, Family, etc.
    cost = db.Column(db.Float, nullable=False)
    billing_cycle = db.Column(db.String(20), default="monthly") # monthly, yearly
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

    # 2. Кредиты
    if request.method == 'POST' and 'credit_name' in request.form:
        try:
            name, c_type = request.form.get('credit_name'), request.form.get('credit_type')
            total, monthly, paid = float(request.form.get('total_amount')), float(request.form.get('monthly_payment')), float(request.form.get('amount_paid', 0))
            db.session.add(Credit(name=name, credit_type=c_type, total_amount=total, monthly_payment=monthly, amount_paid=paid, user_id=user_id))
            db.session.commit()
            flash('Кредит добавлен!', 'success')
        except Exception as e: flash('Ошибка: ' + str(e), 'danger')
        return redirect(url_for('dashboard'))

    # 3. Вклады
    if request.method == 'POST' and 'bank_name' in request.form:
        try:
            bank, desc = request.form.get('bank_name'), request.form.get('description')
            amount, rate, term = float(request.form.get('amount')), float(request.form.get('interest_rate')), int(request.form.get('term_months'))
            db.session.add(Deposit(bank_name=bank, description=desc, amount=amount, interest_rate=rate, term_months=term, user_id=user_id))
            db.session.commit()
            flash('Вклад открыт!', 'success')
        except Exception as e: flash('Ошибка: ' + str(e), 'danger')
        return redirect(url_for('dashboard'))

    # 4. Долги (Мне должны)
    if request.method == 'POST' and 'debtor_name' in request.form:
        try:
            debtor, amount, desc = request.form.get('debtor_name'), float(request.form.get('amount')), request.form.get('description')
            db.session.add(DebtOwed(debtor_name=debtor, amount=amount, description=desc, user_id=user_id))
            db.session.commit()
            flash('Долг записан!', 'success')
        except Exception as e: flash('Ошибка: ' + str(e), 'danger')
        return redirect(url_for('dashboard'))

    # 5. Подписки
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
    
    total_credits_load = sum(c.monthly_payment for c in credits)
    total_deposits_profit = sum(d.total_profit for d in deposits)
    total_debts_owed = sum(d.amount for d in debts if not d.is_paid)
    total_subscriptions = sum(s.cost for s in subscriptions)

    return render_template('dashboard.html', 
                           transactions=transactions, balance=balance, income=income, expense=expense,
                           credits=credits, deposits=deposits, debts=debts, subscriptions=subscriptions,
                           total_credits_load=total_credits_load, total_deposits_profit=total_deposits_profit,
                           total_debts_owed=total_debts_owed, total_subscriptions=total_subscriptions)

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
    if c.user_id == session['user_id']: db.session.delete(c); db.session.commit()
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