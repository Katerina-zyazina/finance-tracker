from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
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
    payment_ref_id = db.Column(db.Integer, nullable=True)
    extra_payment_ref_id = db.Column(db.Integer, nullable=True)

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
    extra_payments = db.relationship('ExtraCreditPayment', backref='credit', lazy=True, order_by="ExtraCreditPayment.date.desc()")

    @property
    def total_with_interest(self):
        if self.interest_rate > 0:
            return self.total_amount * (1 + self.interest_rate / 100)
        return self.total_amount

    @property
    def total_paid(self):
        return sum(p.amount_paid for p in self.payments if p.is_paid)

    @property
    def total_extra_paid(self):
        return sum(p.amount for p in self.extra_payments)

    @property
    def remaining_debt(self):
        return max(0, self.total_with_interest - self.total_paid - self.total_extra_paid)

class CreditPayment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    credit_id = db.Column(db.Integer, db.ForeignKey('credit.id'), nullable=False)
    due_date = db.Column(db.DateTime, nullable=False)
    amount_due = db.Column(db.Float, nullable=False)
    amount_paid = db.Column(db.Float, default=0.0)
    is_paid = db.Column(db.Boolean, default=False)
    note = db.Column(db.String(200))

class ExtraCreditPayment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    credit_id = db.Column(db.Integer, db.ForeignKey('credit.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    date = db.Column(db.DateTime, default=datetime.utcnow)
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
    remaining_balance = total_with_interest - credit.total_paid - credit.total_extra_paid
    
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

def get_recommendations(user_id):
    """Генерирует персональные финансовые рекомендации"""
    recommendations = []
    
    transactions = Transaction.query.filter_by(user_id=user_id).all()
    credits = Credit.query.filter_by(user_id=user_id).all()
    subscriptions = Subscription.query.filter_by(user_id=user_id, is_active=True).all()
    
    income = sum(t.amount for t in transactions if t.type == 'income')
    expense = sum(t.amount for t in transactions if t.type == 'expense')
    balance = income - expense
    
    expenses_by_category = defaultdict(float)
    for t in transactions:
        if t.type == 'expense':
            expenses_by_category[t.category] += t.amount
    
    today = datetime.utcnow()
    upcoming_credit_payments = sum(
        p.amount_due for c in credits 
        for p in c.payments 
        if not p.is_paid and p.due_date >= today and p.due_date < today + timedelta(days=30)
    )
    
    if income > 0 and balance > 0:
        save_amount = min(balance * 0.2, 10000)
        if save_amount >= 1000:
            recommendations.append({
                'icon': '🎯',
                'title': 'Цель на месяц',
                'text': f'Отложи {int(save_amount)}₽ в этом месяце — это укрепит финансовую подушку',
                'type': 'success'
            })
    
    if expenses_by_category:
        top_category = max(expenses_by_category, key=expenses_by_category.get)
        top_amount = expenses_by_category[top_category]
        
        if expense > 0 and top_amount / expense > 0.3:
            recommendations.append({
                'icon': '🔍',
                'title': 'Анализ трат',
                'text': f'На "{top_category}" уходит {int(top_amount)}₽ ({int(top_amount/expense*100)}% расходов). Можно оптимизировать?',
                'type': 'warning'
            })
    
    coffee_keywords = ['кофе', 'кофейня', 'starbucks', 'кофейный']
    for category in expenses_by_category:
        if any(kw in category.lower() for kw in coffee_keywords):
            amount = expenses_by_category[category]
            if amount > 1000:
                recommendations.append({
                    'icon': '☕',
                    'title': 'Экономия на мелочах',
                    'text': f'У тебя много трат на кофе ({int(amount)}₽). Домашний кофе сэкономит до {int(amount*0.7)}₽ в месяц!',
                    'type': 'info'
                })
                break
    
    if credits:
        total_credit_debt = sum(c.remaining_debt for c in credits)
        if total_credit_debt > 50000:
            recommendations.append({
                'icon': '💳',
                'title': 'Кредитная нагрузка',
                'text': f'Общий долг по кредитам: {int(total_credit_debt)}₽. Рассмотрите досрочное погашение для экономии на процентах',
                'type': 'danger'
            })
    
    if subscriptions:
        total_subs = sum(s.cost for s in subscriptions)
        if total_subs > 2000:
            recommendations.append({
                'icon': '📺',
                'title': 'Подписки',
                'text': f'Ежемесячно на подписки уходит {int(total_subs)}₽. Проверь, все ли сервисы ты действительно используешь?',
                'type': 'warning'
            })
    
    if balance < 0:
        recommendations.append({
            'icon': '⚠️',
            'title': 'Внимание',
            'text': f'Расходы превышают доходы на {int(abs(balance))}₽. Пора пересмотреть бюджет',
            'type': 'danger'
        })
    
    if income > 0 and balance < income * 0.1:
        recommendations.append({
            'icon': '🐷',
            'title': 'Накопления',
            'text': 'Попробуй откладывать хотя бы 10% от дохода — через год это даст ощутимый результат',
            'type': 'info'
        })
    
    if len(recommendations) < 2:
        general_tips = [
            {'icon': '📊', 'title': 'Совет', 'text': 'Веди учёт трат ежедневно — это помогает контролировать бюджет', 'type': 'info'},
            {'icon': '🎁', 'title': 'Совет', 'text': 'Создай финансовую цель: отпуск, гаджет, обучение — так легче мотивировать себя экономить', 'type': 'success'},
            {'icon': '🔄', 'title': 'Совет', 'text': 'Раз в месяц пересматривай подписки и кредиты — возможно, найдёшь способ сэкономить', 'type': 'info'},
        ]
        for tip in general_tips:
            if len(recommendations) >= 3:
                break
            if tip not in recommendations:
                recommendations.append(tip)
    
    return recommendations[:3]

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

    if request.method == 'POST' and 'amount' in request.form and 'credit_name' not in request.form and 'bank_name' not in request.form and 'debtor_name' not in request.form and 'service_name' not in request.form and 'payment_id' not in request.form and 'payment_day' not in request.form and 'extra_credit_id' not in request.form:
        try:
            amount, category, trans_type = float(request.form.get('amount')), request.form.get('category'), request.form.get('type')
            if amount <= 0: raise ValueError("Сумма > 0")
            db.session.add(Transaction(amount=amount, category=category, type=trans_type, user_id=user_id))
            db.session.commit()
            flash('Операция добавлена.', 'success')
        except Exception as e: flash('Ошибка: ' + str(e), 'danger')
        return redirect(url_for('dashboard'))

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

    if request.method == 'POST' and 'payment_id' in request.form:
        try:
            pay_id = int(request.form.get('payment_id'))
            paid_amount = float(request.form.get('paid_amount'))
            note = request.form.get('note', '')
            
            payment = CreditPayment.query.get_or_404(pay_id)
            credit = payment.credit
            
            if credit.user_id != user_id:
                flash('Ошибка доступа.', 'danger')
                return redirect(url_for('dashboard'))

            if paid_amount > credit.remaining_debt + 0.01:
                flash(f'Нельзя внести {paid_amount} ₽. Остаток долга: {credit.remaining_debt} ₽', 'danger')
                return redirect(url_for('dashboard'))

            payment.amount_paid = paid_amount
            payment.is_paid = True
            payment.note = note
            
            credit_name = credit.name if credit.name else "Без названия"
            trans = Transaction(
                amount=paid_amount, 
                category=f"Кредит: {credit_name}", 
                type='expense', 
                user_id=user_id,
                payment_ref_id=pay_id
            )
            db.session.add(trans)
            
            generate_payment_schedule(credit)
            db.session.commit()
            flash('Платеж внесен! График пересчитан.', 'success')
        except Exception as e:
            db.session.rollback()
            flash('Ошибка: ' + str(e), 'danger')
        return redirect(url_for('dashboard'))

    if request.method == 'POST' and 'extra_credit_id' in request.form:
        try:
            credit_id = int(request.form.get('extra_credit_id'))
            extra_amount = float(request.form.get('extra_amount'))
            extra_note = request.form.get('extra_note', '')
            
            credit = Credit.query.get_or_404(credit_id)
            if credit.user_id != user_id:
                flash('Ошибка доступа.', 'danger')
                return redirect(url_for('dashboard'))

            if extra_amount > credit.remaining_debt + 0.01:
                flash(f'Нельзя внести {extra_amount} ₽. Остаток долга: {credit.remaining_debt} ₽', 'danger')
                return redirect(url_for('dashboard'))

            extra_payment = ExtraCreditPayment(
                credit_id=credit.id,
                amount=extra_amount,
                note=extra_note
            )
            db.session.add(extra_payment)
            db.session.flush()
            
            credit_name = credit.name if credit.name else "Без названия"
            trans = Transaction(
                amount=extra_amount, 
                category=f"Досрочно: {credit_name}", 
                type='expense', 
                user_id=user_id,
                extra_payment_ref_id=extra_payment.id
            )
            db.session.add(trans)
            
            generate_payment_schedule(credit)
            db.session.commit()
            flash(f'Досрочный платёж {extra_amount} ₽ внесён! График пересчитан.', 'success')
        except Exception as e:
            db.session.rollback()
            flash('Ошибка: ' + str(e), 'danger')
        return redirect(url_for('dashboard'))

    if request.method == 'POST' and 'bank_name' in request.form:
        try:
            bank, desc = request.form.get('bank_name'), request.form.get('description')
            amount, rate, term = float(request.form.get('amount')), float(request.form.get('interest_rate')), int(request.form.get('term_months'))
            db.session.add(Deposit(bank_name=bank, description=desc, amount=amount, interest_rate=rate, term_months=term, user_id=user_id))
            db.session.commit()
            flash('Вклад открыт!', 'success')
        except Exception as e: flash('Ошибка: ' + str(e), 'danger')
        return redirect(url_for('dashboard'))

    if request.method == 'POST' and 'debtor_name' in request.form:
        try:
            debtor, amount, desc = request.form.get('debtor_name'), float(request.form.get('amount')), request.form.get('description')
            db.session.add(DebtOwed(debtor_name=debtor, amount=amount, description=desc, user_id=user_id))
            db.session.commit()
            flash('Долг записан!', 'success')
        except Exception as e: flash('Ошибка: ' + str(e), 'danger')
        return redirect(url_for('dashboard'))

    if request.method == 'POST' and 'service_name' in request.form:
        try:
            service, plan = request.form.get('service_name'), request.form.get('plan_name')
            cost, cycle = float(request.form.get('cost')), request.form.get('billing_cycle')
            db.session.add(Subscription(service_name=service, plan_name=plan, cost=cost, billing_cycle=cycle, user_id=user_id))
            db.session.commit()
            flash('Подписка добавлена!', 'success')
        except Exception as e: flash('Ошибка: ' + str(e), 'danger')
        return redirect(url_for('dashboard'))

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
    recommendations = get_recommendations(user_id)

    return render_template('dashboard.html', 
                           transactions=transactions, balance=balance, income=income, expense=total_expenses,
                           credits=credits, deposits=deposits, debts=debts, subscriptions=subscriptions,
                           upcoming_payments=upcoming_payments, total_subscriptions=subscriptions_total,
                           total_debts_owed=total_debts_owed, now=datetime.utcnow(), 
                           chart_data=chart_data, recommendations=recommendations)

# ================= ПОИСК И ФИЛЬТРАЦИЯ =================

@app.route('/api/search/transactions')
@login_required
def search_transactions():
    """API для фильтрации транзакций"""
    user_id = session['user_id']
    
    trans_type = request.args.get('type', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    min_amount = request.args.get('min_amount', type=float)
    max_amount = request.args.get('max_amount', type=float)
    category = request.args.get('category', '')
    sort = request.args.get('sort', 'newest')
    
    query = Transaction.query.filter_by(user_id=user_id)
    
    if trans_type:
        query = query.filter_by(type=trans_type)
    
    if category:
        query = query.filter_by(category=category)
    
    if date_from:
        try:
            date_from_dt = datetime.strptime(date_from, '%Y-%m-%d')
            query = query.filter(Transaction.date >= date_from_dt)
        except:
            pass
    
    if date_to:
        try:
            date_to_dt = datetime.strptime(date_to, '%Y-%m-%d')
            query = query.filter(Transaction.date <= date_to_dt)
        except:
            pass
    
    if min_amount is not None:
        query = query.filter(Transaction.amount >= min_amount)
    
    if max_amount is not None:
        query = query.filter(Transaction.amount <= max_amount)
    
    if sort == 'newest':
        query = query.order_by(Transaction.date.desc())
    elif sort == 'oldest':
        query = query.order_by(Transaction.date.asc())
    elif sort == 'amount':
        query = query.order_by(Transaction.amount.desc())
    
    transactions = query.all()
    
    result = []
    for t in transactions:
        result.append({
            'id': t.id,
            'amount': t.amount,
            'category': t.category,
            'type': t.type,
            'date': t.date.strftime('%d.%m.%Y'),
            'date_raw': t.date.isoformat()
        })
    
    return {'transactions': result, 'total': len(result)}

@app.route('/api/search/credits')
@login_required
def search_credits():
    """API для поиска кредитов"""
    user_id = session['user_id']
    search = request.args.get('search', '').strip()
    
    query = Credit.query.filter_by(user_id=user_id)
    
    if search:
        search_pattern = f'%{search}%'
        query = query.filter(Credit.name.ilike(search_pattern))
    
    credits = query.all()
    
    result = []
    for c in credits:
        result.append({
            'id': c.id,
            'name': c.name,
            'total_amount': c.total_amount,
            'remaining_debt': c.remaining_debt,
            'interest_rate': c.interest_rate,
            'monthly_payment': c.monthly_payment_fixed
        })
    
    return {'credits': result, 'total': len(result)}

@app.route('/api/search/deposits')
@login_required
def search_deposits():
    """API для поиска вкладов"""
    user_id = session['user_id']
    search = request.args.get('search', '').strip()
    
    query = Deposit.query.filter_by(user_id=user_id)
    
    if search:
        search_pattern = f'%{search}%'
        query = query.filter(
            db.or_(
                Deposit.bank_name.ilike(search_pattern),
                Deposit.description.ilike(search_pattern)
            )
        )
    
    deposits = query.all()
    
    result = []
    for d in deposits:
        result.append({
            'id': d.id,
            'bank_name': d.bank_name,
            'description': d.description,
            'amount': d.amount,
            'interest_rate': d.interest_rate,
            'term_months': d.term_months,
            'total_amount_end': d.total_amount_end
        })
    
    return {'deposits': result, 'total': len(result)}

@app.route('/api/search/debts')
@login_required
def search_debts():
    """API для поиска долгов"""
    user_id = session['user_id']
    search = request.args.get('search', '').strip()
    
    query = DebtOwed.query.filter_by(user_id=user_id)
    
    if search:
        search_pattern = f'%{search}%'
        query = query.filter(
            db.or_(
                DebtOwed.debtor_name.ilike(search_pattern),
                DebtOwed.description.ilike(search_pattern)
            )
        )
    
    debts = query.all()
    
    result = []
    for d in debts:
        result.append({
            'id': d.id,
            'debtor_name': d.debtor_name,
            'amount': d.amount,
            'description': d.description,
            'is_paid': d.is_paid
        })
    
    return {'debts': result, 'total': len(result)}

@app.route('/api/search/subscriptions')
@login_required
def search_subscriptions():
    """API для поиска подписок"""
    user_id = session['user_id']
    search = request.args.get('search', '').strip()
    
    query = Subscription.query.filter_by(user_id=user_id)
    
    if search:
        search_pattern = f'%{search}%'
        query = query.filter(
            db.or_(
                Subscription.service_name.ilike(search_pattern),
                Subscription.plan_name.ilike(search_pattern)
            )
        )
    
    subscriptions = query.all()
    
    result = []
    for s in subscriptions:
        result.append({
            'id': s.id,
            'service_name': s.service_name,
            'plan_name': s.plan_name,
            'cost': s.cost,
            'billing_cycle': s.billing_cycle
        })
    
    return {'subscriptions': result, 'total': len(result)}

@app.route('/api/statistics/categories')
@login_required
def get_categories():
    """API для получения списка категорий"""
    user_id = session['user_id']
    
    categories = db.session.query(Transaction.category).filter_by(
        user_id=user_id, type='expense'
    ).distinct().all()
    
    category_list = [cat[0] for cat in categories if cat[0]]
    
    return {'categories': sorted(category_list)}

# ================= УДАЛЕНИЯ =================

@app.route('/delete_trans/<int:id>')
@login_required
def delete_transaction(id):
    t = Transaction.query.get_or_404(id)
    if t.user_id == session['user_id']:
        if t.payment_ref_id:
            payment = CreditPayment.query.get(t.payment_ref_id)
            if payment and payment.is_paid:
                payment.is_paid = False
                payment.amount_paid = 0.0
                generate_payment_schedule(payment.credit)
        
        if t.extra_payment_ref_id:
            extra_payment = ExtraCreditPayment.query.get(t.extra_payment_ref_id)
            if extra_payment:
                credit = extra_payment.credit
                db.session.delete(extra_payment)
                generate_payment_schedule(credit)
        
        db.session.delete(t)
        db.session.commit()
        flash('Операция удалена.', 'info')
    return redirect(url_for('dashboard'))

@app.route('/delete_extra/<int:id>')
@login_required
def delete_extra_payment(id):
    extra = ExtraCreditPayment.query.get_or_404(id)
    credit = extra.credit
    if credit.user_id == session['user_id']:
        db.session.delete(extra)
        generate_payment_schedule(credit)
        db.session.commit()
        flash('Досрочный платёж удалён.', 'info')
    return redirect(url_for('dashboard'))

@app.route('/delete_credit/<int:id>')
@login_required
def delete_credit(id):
    c = Credit.query.get_or_404(id)
    if c.user_id == session['user_id']: 
        for p in c.payments: db.session.delete(p)
        for ep in c.extra_payments: db.session.delete(ep)
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