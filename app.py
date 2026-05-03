from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import os

# Создание приложения Flask
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-me')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///finance.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# Модели базы данных
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    transactions = db.relationship('Transaction', backref='user', lazy=True)
    credits = db.relationship('Credit', backref='user', lazy=True)

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
    monthly_payment = db.Column(db.Float, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

# Декоратор проверки авторизации
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Пожалуйста, войдите в систему.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# Маршруты
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

    # Обработка добавления ТРАНЗАКЦИИ
    if request.method == 'POST' and 'amount' in request.form:
        try:
            amount = float(request.form.get('amount'))
            category = request.form.get('category')
            trans_type = request.form.get('type')
            
            if amount <= 0:
                raise ValueError("Сумма должна быть положительной")

            new_trans = Transaction(amount=amount, category=category, type=trans_type, user_id=user_id)
            db.session.add(new_trans)
            db.session.commit()
            flash('Операция добавлена.', 'success')
        except Exception as e:
            flash('Ошибка: введите корректные данные.', 'danger')
        return redirect(url_for('dashboard'))

    # Обработка добавления КРЕДИТА
    if request.method == 'POST' and 'credit_name' in request.form:
        try:
            c_name = request.form.get('credit_name')
            c_total = float(request.form.get('credit_total'))
            c_monthly = float(request.form.get('credit_monthly'))
            
            new_credit = Credit(name=c_name, total_amount=c_total, monthly_payment=c_monthly, user_id=user_id)
            db.session.add(new_credit)
            db.session.commit()
            flash('Кредит добавлен в учет!', 'success')
        except Exception as e:
            flash('Ошибка при добавлении кредита.', 'danger')
        return redirect(url_for('dashboard'))

    # Получение данных
    transactions = Transaction.query.filter_by(user_id=user_id).order_by(Transaction.date.desc()).all()
    credits = Credit.query.filter_by(user_id=user_id).all()

    income = sum(t.amount for t in transactions if t.type == 'income')
    expense = sum(t.amount for t in transactions if t.type == 'expense')
    balance = income - expense
    
    # Считаем общую нагрузку по кредитам
    total_debt_load = sum(c.monthly_payment for c in credits)
    total_debt_amount = sum(c.total_amount for c in credits)

    return render_template('dashboard.html', 
                           transactions=transactions, 
                           balance=balance, 
                           income=income, 
                           expense=expense,
                           credits=credits,
                           total_debt_load=total_debt_load,
                           total_debt_amount=total_debt_amount)

@app.route('/delete/<int:id>')
@login_required
def delete_transaction(id):
    trans = Transaction.query.get_or_404(id)
    if trans.user_id == session['user_id']:
        db.session.delete(trans)
        db.session.commit()
        flash('Запись удалена.', 'info')
    return redirect(url_for('dashboard'))

@app.route('/delete_credit/<int:id>')
@login_required
def delete_credit(id):
    credit = Credit.query.get_or_404(id)
    if credit.user_id == session['user_id']:
        db.session.delete(credit)
        db.session.commit()
        flash('Кредит закрыт и удален.', 'success')
    return redirect(url_for('dashboard'))

# Запуск приложения
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)