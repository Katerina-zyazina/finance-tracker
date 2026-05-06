from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import os
from datetime import datetime

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

# --- МОДЕЛИ БАЗЫ ДАННЫХ ---

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    transactions = db.relationship('Transaction', backref='user', lazy=True)
    credits = db.relationship('Credit', backref='user', lazy=True)
    deposits = db.relationship('Deposit', backref='user', lazy=True)
    debts_owed = db.relationship('DebtOwed', backref='user', lazy=True)

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
    credit_type = db.Column(db.String(50), default="installment") # mortgage, installment, loan
    total_amount = db.Column(db.Float, nullable=False)
    monthly_payment = db.Column(db.Float, nullable=False)
    amount_paid = db.Column(db.Float, default=0.0) # Сколько уже выплачено
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    # Вычисляемое свойство: Остаток долга
    @property
    def remaining_amount(self):
        return self.total_amount - self.amount_paid

    # Вычисляемое свойство: Сколько месяцев осталось
    @property
    def months_left(self):
        if self.monthly_payment > 0:
            return round(self.remaining_amount / self.monthly_payment, 1)
        return 0

# Новая модель Вкладов
class Deposit(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    bank_name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(100), nullable=False)
    amount = db.Column(db.Float, nullable=False) # Начальная сумма
    interest_rate = db.Column(db.Float, nullable=False) # Годовая ставка %
    term_months = db.Column(db.Integer, nullable=False) # Срок в месяцах
    date_created = db.Column(db.DateTime, default=db.func.current_timestamp())
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    # Вычисляемое свойство: Итого к получению (простой процент)
    @property
    def total_profit(self):
        # Формула: Сумма * (Ставка/100) * (Срок/12)
        return self.amount * (self.interest_rate / 100) * (self.term_months / 12)

    @property
    def total_amount_end(self):
        return self.amount + self.total_profit

# Новая модель Долгов (Мне должны)
class DebtOwed(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    debtor_name = db.Column(db.String(100), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    description = db.Column(db.String(200))
    is_paid = db.Column(db.Boolean, default=False)
    date_created = db.Column(db.DateTime, default=db.func.current_timestamp())
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

# --- ДЕКОРАТОРЫ И МАРШРУТЫ ---

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Пожалуйста, войдите в систему.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/')
def home():
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if User.query.filter_by(username=username).first():
            flash('Логин занят.', 'danger')
            return redirect(url_for('register'))
        
        new_user = User(username=username, password_hash=generate_password_hash(password))
        db.session.add(new_user)
        db.session.commit()
        flash('Регистрация успешна!', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        
        if user and check_password_hash(user.password_hash, password):
            session['user_id'] = user.id
            session['username'] = user.username
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

    # 1. Обработка ТРАНЗАКЦИЙ
    if request.method == 'POST' and 'amount' in request.form and 'credit_name' not in request.form and 'bank_name' not in request.form and 'debtor_name' not in request.form:
        try:
            amount = float(request.form.get('amount'))
            category = request.form.get('category')
            trans_type = request.form.get('type')
            if amount <= 0: raise ValueError("Сумма > 0")
            new_trans = Transaction(amount=amount, category=category, type=trans_type, user_id=user_id)
            db.session.add(new_trans)
            db.session.commit()
            flash('Операция добавлена.', 'success')
        except Exception as e: flash('Ошибка: ' + str(e), 'danger')
        return redirect(url_for('dashboard'))

    # 2. Обработка КРЕДИТОВ
    if request.method == 'POST' and 'credit_name' in request.form:
        try:
            name = request.form.get('credit_name')
            c_type = request.form.get('credit_type')
            total = float(request.form.get('total_amount'))
            monthly = float(request.form.get('monthly_payment'))
            paid = float(request.form.get('amount_paid', 0))
            
            new_credit = Credit(name=name, credit_type=c_type, total_amount=total, monthly_payment=monthly, amount_paid=paid, user_id=user_id)
            db.session.add(new_credit)
            db.session.commit()
            flash('Кредит добавлен!', 'success')
        except Exception as e: flash('Ошибка: ' + str(e), 'danger')
        return redirect(url_for('dashboard'))

    # 3. Обработка ВКЛАДОВ
    if request.method == 'POST' and 'bank_name' in request.form:
        try:
            bank = request.form.get('bank_name')
            desc = request.form.get('description')
            amount = float(request.form.get('amount'))
            rate = float(request.form.get('interest_rate'))
            term = int(request.form.get('term_months'))
            
            new_dep = Deposit(bank_name=bank, description=desc, amount=amount, interest_rate=rate, term_months=term, user_id=user_id)
            db.session.add(new_dep)
            db.session.commit()
            flash('Вклад открыт!', 'success')
        except Exception as e: flash('Ошибка: ' + str(e), 'danger')
        return redirect(url_for('dashboard'))

    # 4. Обработка ДОЛГОВ (Мне должны)
    if request.method == 'POST' and 'debtor_name' in request.form:
        try:
            debtor = request.form.get('debtor_name')
            amount = float(request.form.get('amount'))
            desc = request.form.get('description')
            
            new_debt = DebtOwed(debtor_name=debtor, amount=amount, description=desc, user_id=user_id)
            db.session.add(new_debt)
            db.session.commit()
            flash('Долг записан!', 'success')
        except Exception as e: flash('Ошибка: ' + str(e), 'danger')
        return redirect(url_for('dashboard'))

    # Сбор данных для отображения
    transactions = Transaction.query.filter_by(user_id=user_id).order_by(Transaction.date.desc()).all()
    
    # Кредиты (получаем все, фильтр будем делать в HTML или тут)
    all_credits = Credit.query.filter_by(user_id=user_id).all()
    
    # Вклады
    deposits = Deposit.query.filter_by(user_id=user_id).all()
    
    # Долги
    debts = DebtOwed.query.filter_by(user_id=user_id).all()

    # Сводка
    income = sum(t.amount for t in transactions if t.type == 'income')
    expense = sum(t.amount for t in transactions if t.type == 'expense')
    balance = income - expense
    
    # Сводка по долгам и вкладам
    total_credits_load = sum(c.monthly_payment for c in all_credits)
    total_deposits_profit = sum(d.total_profit for d in deposits)
    total_deposits_end = sum(d.total_amount_end for d in deposits)
    total_debts_owed = sum(d.amount for d in debts if not d.is_paid)

    return render_template('dashboard.html', 
                           transactions=transactions, 
                           balance=balance, income=income, expense=expense,
                           credits=all_credits,
                           deposits=deposits,
                           debts=debts,
                           total_credits_load=total_credits_load,
                           total_deposits_profit=total_deposits_profit,
                           total_deposits_end=total_deposits_end,
                           total_debts_owed=total_debts_owed)

# Удаление записей
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

# Создание таблиц
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=True)