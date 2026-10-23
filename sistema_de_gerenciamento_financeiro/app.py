import os
import calendar
import pandas as pd
import io
from datetime import date, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin, login_user, LoginManager, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy.orm import joinedload 
from sqlalchemy import extract, func 

app = Flask(__name__)

# --- Configuração de Segurança e Banco de Dados ---
basedir = os.path.abspath(os.path.dirname(__file__))

database_url = os.environ.get('DATABASE_URL')
if database_url:
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'project.db')

app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'uma-chave-secreta-padrao-para-desenvolvimento')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id)) if user_id else None

# --- Modelos ---
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(20), nullable=False, unique=True)
    password = db.Column(db.String(256), nullable=False) 
    is_admin = db.Column(db.Boolean, default=False)

class Cliente(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False, unique=True)
    telefone = db.Column(db.String(20), nullable=True)

class Pedido(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    numero_pedido = db.Column(db.String(50), nullable=False, unique=True, index=True) 
    cliente_nome = db.Column(db.String(100), nullable=False)
    vendedor = db.Column(db.String(20), nullable=True) 
    valor_total = db.Column(db.Float, nullable=False)
    forma_pagamento = db.Column(db.String(50), nullable=False)
    tipo_produto = db.Column(db.String(10), nullable=True) 
    num_parcelas = db.Column(db.Integer)
    data_emissao = db.Column(db.Date, nullable=False) 
    data_lancamento = db.Column(db.Date, nullable=False) 
    criado_por = db.Column(db.String(20), nullable=True)
    parcelas = db.relationship('Parcela', backref='pedido', lazy=True, cascade="all, delete-orphan") 

class Parcela(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    valor = db.Column(db.Float, nullable=False)
    data_vencimento = db.Column(db.Date, nullable=False, index=True) 
    status = db.Column(db.String(50), nullable=False, default='Pendente')
    parcela_num = db.Column(db.Integer, nullable=False)
    pedido_id = db.Column(db.Integer, db.ForeignKey('pedido.id'), nullable=False)

# --- Auxiliares ---
def add_months(sourcedate, months):
    month = sourcedate.month - 1 + months
    year = sourcedate.year + month // 12
    month = month % 12 + 1
    day = min(sourcedate.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)

# --- Rotas de Autenticação ---
@app.route('/')
def index():
    alerta_parcelas = []
    resumo_mensal = []
    total_filtrado_mes = None
    hoje = date.today()

    # Parâmetros de filtro recebidos via GET na página inicial
    filtro_mes = request.args.get('filtro_mes', default=hoje.month, type=int)
    filtro_ano = request.args.get('filtro_ano', default=hoje.year, type=int)
    
    if current_user.is_authenticated:
        data_limite = hoje + timedelta(days=45)
        alerta_parcelas = Parcela.query.join(Pedido).options(joinedload(Parcela.pedido)).filter(
            Parcela.status == 'Pendente',
            Parcela.data_vencimento >= hoje,
            Parcela.data_vencimento <= data_limite
        ).order_by(Parcela.data_vencimento).all()

        # --- Cálculo do valor total pendente para o mês/ano selecionado no filtro ---
        parcelas_filtradas = Parcela.query.filter(
            Parcela.status == 'Pendente',
            extract('month', Parcela.data_vencimento) == filtro_mes,
            extract('year', Parcela.data_vencimento) == filtro_ano
        ).all()
        total_filtrado_mes = sum(p.valor for p in parcelas_filtradas)

        # --- Cálculo dos valores a receber agrupados por todos os meses (Apenas Pendentes) ---
        resultados_mensais = db.session.query(
            extract('year', Parcela.data_vencimento).label('ano'),
            extract('month', Parcela.data_vencimento).label('mes'),
            func.sum(Parcela.valor).label('total')
        ).filter(Parcela.status == 'Pendente'
        ).group_by('ano', 'mes'
        ).order_by('ano', 'mes').all()

        meses_pt = {
            1: 'Janeiro', 2: 'Fevereiro', 3: 'Março', 4: 'Abril',
            5: 'Maio', 6: 'Junho', 7: 'Julho', 8: 'Agosto',
            9: 'Setembro', 10: 'Outubro', 11: 'Novembro', 12: 'Dezembro'
        }

        for r in resultados_mensais:
            ano = int(r.ano)
            mes_num = int(r.mes)
            nome_mes = f"{meses_pt.get(mes_num, '')}/{ano}"
            resumo_mensal.append({
                'mes_ano': nome_mes,
                'mes_num': mes_num,
                'ano': ano,
                'total': r.total
            })

    return render_template('index.html', 
                           alerta_parcelas=alerta_parcelas, 
                           resumo_mensal=resumo_mensal,
                           total_filtrado_mes=total_filtrado_mes,
                           filtro_mes=filtro_mes,
                           filtro_ano=filtro_ano,
                           hoje=hoje)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated: return redirect(url_for('index'))
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form.get('username')).first()
        if user and check_password_hash(user.password, request.form.get('password')):
            login_user(user)
            return redirect(url_for('index'))
        flash('Credenciais inválidas.', 'error')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if User.query.count() > 0: return redirect(url_for('login'))
    if request.method == 'POST':
        hashed = generate_password_hash(request.form.get('password'), method='scrypt')
        new_u = User(username=request.form.get('username'), password=hashed, is_admin=True)
        db.session.add(new_u)
        db.session.commit()
        return redirect(url_for('login'))
    return render_template('register.html')

# --- Gestão de Clientes ---
@app.route('/clientes', methods=['GET', 'POST'])
@login_required
def clientes():
    if request.method == 'POST':
        nome_novo = request.form.get('nome').strip().upper()
        existente = Cliente.query.filter_by(nome=nome_novo).first()
        if existente:
            flash(f'O cliente "{nome_novo}" já está cadastrado!', 'error')
        else:
            try:
                db.session.add(Cliente(nome=nome_novo, telefone=request.form.get('telefone')))
                db.session.commit()
                flash('Cliente cadastrado!', 'success')
            except Exception as e:
                db.session.rollback()
                flash('Erro ao salvar cliente.', 'error')
            
    lista_clientes = Cliente.query.all()
    clientes_processados = []
    hoje = date.today()
    for c in lista_clientes:
        ult = Pedido.query.filter_by(cliente_nome=c.nome).order_by(Pedido.data_lancamento.desc()).first()
        dias = (hoje - ult.data_lancamento).days if ult else None
        clientes_processados.append({'info': c, 'dias_inatividade': dias})
    return render_template('clientes.html', clientes=clientes_processados)

@app.route('/editar_cliente/<int:id>', methods=['GET', 'POST'])
@login_required
def editar_cliente(id):
    cliente = Cliente.query.get_or_404(id)
    if request.method == 'POST':
        cliente.telefone = request.form.get('telefone')
        db.session.commit()
        flash('Cliente atualizado!', 'success')
        return redirect(url_for('clientes'))
    return render_template('editar_cliente.html', cliente=cliente)

@app.route('/excluir_cliente/<int:id>')
@login_required
def excluir_cliente(id):
    cliente = Cliente.query.get_or_404(id)
    db.session.delete(cliente)
    db.session.commit()
    flash('Cliente removido!', 'success')
    return redirect(url_for('clientes'))

# --- Gestão de Pedidos ---
@app.route('/pedidos', methods=['GET', 'POST'])
@login_required
def pedidos():
    lista_c = Cliente.query.order_by(Cliente.nome).all()
    if request.method == 'POST':
        try:
            val = float(request.form.get('valor'))
            num_p = int(request.form.get('num_parcelas') or 1)
            data_v = date.fromisoformat(request.form.get('data_vencimento'))
            data_e_str = request.form.get('data_emissao')
            data_e = date.fromisoformat(data_e_str) if data_e_str else date.today()

            novo = Pedido(
                numero_pedido=request.form.get('numero_pedido'), 
                cliente_nome=request.form.get('cliente_nome'), 
                vendedor=request.form.get('vendedor'),
                valor_total=val, forma_pagamento=request.form.get('forma_pagamento'), 
                tipo_produto=request.form.get('tipo_produto'),
                num_parcelas=num_p, data_emissao=data_e, 
                data_lancamento=date.today(), criado_por=current_user.username
            )
            db.session.add(novo); db.session.flush()
            p_val = round(val / num_p, 2); dif = round(val - (p_val * num_p), 2)
            for i in range(num_p):
                db.session.add(Parcela(valor=round(p_val + (dif if i == 0 else 0), 2), 
                                      data_vencimento=add_months(data_v, i), parcela_num=i+1, pedido_id=novo.id))
            db.session.commit(); flash('Pedido lançado!', 'success')
        except Exception as e: db.session.rollback(); flash(str(e), 'error')
    return render_template('pedidos.html', pedidos=Pedido.query.order_by(Pedido.id.desc()).all(), clientes=lista_c)

@app.route('/editar_pedido/<int:id>', methods=['GET', 'POST'])
@login_required
def editar_pedido(id):
    pedido = Pedido.query.get_or_404(id)
    if request.method == 'POST':
        try:
            val = float(request.form.get('valor'))
            num_p = int(request.form.get('num_parcelas') or 1)
            data_v = date.fromisoformat(request.form.get('data_vencimento'))
            data_e_str = request.form.get('data_emissao')
            data_e = date.fromisoformat(data_e_str) if data_e_str else pedido.data_emissao

            pedido.numero_pedido = request.form.get('numero_pedido')
            pedido.cliente_nome = request.form.get('cliente_nome')
            pedido.vendedor = request.form.get('vendedor')
            pedido.valor_total = val
            pedido.forma_pagamento = request.form.get('forma_pagamento')
            pedido.tipo_produto = request.form.get('tipo_produto')
            pedido.num_parcelas = num_p
            pedido.data_emissao = data_e

            Parcela.query.filter_by(pedido_id=pedido.id).delete()

            p_val = round(val / num_p, 2)
            dif = round(val - (p_val * num_p), 2)
            for i in range(num_p):
                db.session.add(Parcela(
                    valor=round(p_val + (dif if i == 0 else 0), 2), 
                    data_vencimento=add_months(data_v, i), 
                    parcela_num=i+1, 
                    pedido_id=pedido.id,
                    status='Pendente'
                ))
            db.session.commit()
            flash('Pedido atualizado com sucesso!', 'success')
            return redirect(url_for('pedidos'))
        except Exception as e:
            db.session.rollback()
            flash('Erro ao editar pedido: ' + str(e), 'error')
    return render_template('editar_pedido.html', pedido=pedido)

@app.route('/excluir_pedido/<int:id>')
@login_required
def excluir_pedido(id):
    p = Pedido.query.get_or_404(id)
    db.session.delete(p); db.session.commit()
    flash('Pedido excluído!', 'success')
    return redirect(url_for('pedidos'))

@app.route('/excluir_pedidos_em_massa', methods=['POST'])
@login_required
def excluir_pedidos_em_massa():
    ids = request.form.getlist('pedidos_ids')
    if ids:
        try:
            pedidos_para_excluir = Pedido.query.filter(Pedido.id.in_([int(i) for i in ids])).all()
            for pedido in pedidos_para_excluir:
                db.session.delete(pedido)
            db.session.commit()
            flash(f'{len(ids)} pedidos excluídos!', 'success')
        except Exception as e:
            db.session.rollback(); flash('Erro ao excluir: ' + str(e), 'error')
    return redirect(url_for('pedidos'))

# --- Gestão Financeira & Dashboard ---
@app.route('/gestao_financeira')
@login_required
def gestao_financeira():
    hoje = date.today()
    mes = request.args.get('mes', default=hoje.month, type=int)
    ano = request.args.get('ano', default=hoje.year, type=int)

    pedidos_mes = Pedido.query.filter(
        extract('month', Pedido.data_emissao) == mes,
        extract('year', Pedido.data_emissao) == ano
    ).all()

    total_vendido_mes = sum(p.valor_total for p in pedidos_mes)
    total_seco = sum(p.valor_total for p in pedidos_mes if p.tipo_produto == 'Seco')
    total_gelado = sum(p.valor_total for p in pedidos_mes if p.tipo_produto == 'Gelado')

    vendas_vendedores = {}
    for p in pedidos_mes:
        vendas_vendedores[p.vendedor] = vendas_vendedores.get(p.vendedor, 0) + p.valor_total

    todas_parcelas = Parcela.query.join(Pedido).all()
    parcelas_mes = Parcela.query.join(Pedido).filter(
        extract('month', Parcela.data_vencimento) == mes, 
        extract('year', Parcela.data_vencimento) == ano
    ).all()

    return render_template('gestao_financeira.html', 
                           parcelas_do_mes=parcelas_mes, 
                           total_a_receber=sum(p.valor for p in todas_parcelas if p.status == 'Pendente'), 
                           total_baixado=sum(p.valor for p in todas_parcelas if p.status == 'Baixado'),
                           total_vendido_mes=total_vendido_mes,
                           total_seco=total_seco, total_gelado=total_gelado,
                           vendas_vendedores=vendas_vendedores,
                           mes=mes, ano=ano)

@app.route('/dar_baixa_parcela/<int:id>', methods=['POST'])
@login_required
def dar_baixa_parcela(id):
    Parcela.query.get_or_404(id).status = 'Baixado'; db.session.commit()
    return redirect(request.referrer or url_for('gestao_financeira'))

@app.route('/retirar_baixa_parcela/<int:id>', methods=['POST'])
@login_required
def retirar_baixa_parcela(id):
    Parcela.query.get_or_404(id).status = 'Pendente'; db.session.commit()
    return redirect(request.referrer or url_for('gestao_financeira'))

@app.route('/excluir_parcela/<int:id>', methods=['POST'])
@login_required
def excluir_parcela(id):
    try:
        parcela = Parcela.query.get_or_404(id)
        pedido = parcela.pedido
        
        db.session.delete(parcela)
        
        pedido.num_parcelas = max(0, len(pedido.parcelas) - 1)
        pedido.valor_total = max(0.0, round(pedido.valor_total - parcela.valor, 2))
        
        db.session.commit()
        flash('Parcela excluída e pedido atualizado com sucesso!', 'success')
    except Exception as e:
        db.session.rollback()
        flash('Erro ao excluir parcela: ' + str(e), 'error')
    return redirect(url_for('gestao_financeira'))

# --- Administração de Usuários ---
@app.route('/admin_users')
@login_required
def admin_users():
    if not current_user.is_admin: return redirect(url_for('index'))
    return render_template('admin_users.html', usuarios=User.query.all(), user_count=User.query.count())

@app.route('/criar_usuario', methods=['POST'])
@login_required
def criar_usuario():
    if not current_user.is_admin or User.query.count() >= 6: return redirect(url_for('admin_users'))
    hashed = generate_password_hash(request.form.get('password'), method='scrypt')
    ser_admin = True if request.form.get('is_admin') == 'on' else False
    db.session.add(User(username=request.form.get('username'), password=hashed, is_admin=ser_admin))
    db.session.commit()
    flash('Usuário criado!', 'success')
    return redirect(url_for('admin_users'))

@app.route('/toggle_admin/<int:user_id>', methods=['POST'])
@login_required
def toggle_admin(user_id):
    if not current_user.is_admin: return redirect(url_for('index'))
    u = User.query.get_or_404(user_id)
    if u.id != current_user.id:
        u.is_admin = not u.is_admin
        db.session.commit()
    return redirect(url_for('admin_users'))

@app.route('/reset_password/<int:user_id>', methods=['POST'])
@login_required
def reset_password(user_id):
    if not current_user.is_admin: return redirect(url_for('index'))
    u = User.query.get_or_404(user_id)
    u.password = generate_password_hash(request.form.get('new_password'), method='scrypt')
    db.session.commit()
    flash('Senha redefinida!', 'success')
    return redirect(url_for('admin_users'))

@app.route('/delete_user/<int:user_id>', methods=['POST'])
@login_required
def delete_user(user_id):
    if not current_user.is_admin: return redirect(url_for('index'))
    u = User.query.get_or_404(user_id)
    if u.id != current_user.id:
        db.session.delete(u); db.session.commit()
        flash('Usuário excluído!', 'success')
    return redirect(url_for('admin_users'))

# --- Exportações ---
@app.route('/exportar_pedidos')
@login_required
def exportar_pedidos():
    parcelas = Parcela.query.join(Pedido).all()
    df = pd.DataFrame([{
        'Pedido': p.pedido.numero_pedido, 'Emissão': p.pedido.data_emissao, 'Cliente': p.pedido.cliente_nome, 
        'Vendedor': p.pedido.vendedor, 'Tipo': p.pedido.tipo_produto, 'Valor': p.valor, 
        'Vencimento': p.data_vencimento, 'Status': p.status
    } for p in parcelas])
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine='openpyxl') as writer: df.to_excel(writer, index=False)
    out.seek(0); return send_file(out, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name='pedidos.xlsx')

@app.route('/exportar_todos_pedidos_lancados')
@login_required
def exportar_todos_pedidos_lancados():
    lista_pedidos = Pedido.query.order_by(Pedido.id.desc()).all()
    df = pd.DataFrame([{
        'Nº Pedido': p.numero_pedido,
        'Data Emissão': p.data_emissao.strftime('%d/%m/%Y') if p.data_emissao else '',
        'Cliente': p.cliente_nome,
        'Vendedor': p.vendedor,
        'Tipo Produto': p.tipo_produto,
        'Forma Pagamento': p.forma_pagamento,
        'Nº Parcelas': p.num_parcelas,
        'Valor Total': p.valor_total,
        'Criado Por': p.criado_por,
        'Data Lançamento': p.data_lancamento.strftime('%d/%m/%Y') if p.data_lancamento else ''
    } for p in lista_pedidos])
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine='openpyxl') as writer: df.to_excel(writer, index=False)
    out.seek(0); return send_file(out, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name='todos_pedidos_lancados.xlsx')

@app.route('/exportar_clientes')
@login_required
def exportar_clientes():
    df = pd.DataFrame([{'Nome': c.nome, 'Telefone': c.telefone} for c in Cliente.query.all()])
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine='openpyxl') as writer: df.to_excel(writer, index=False)
    out.seek(0); return send_file(out, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name='clientes.xlsx')

@app.route('/exportar_alerta_vencimento')
@login_required
def exportar_alerta_vencimento():
    hoje = date.today(); data_limite = hoje + timedelta(days=45)
    parcelas = Parcela.query.join(Pedido).filter(Parcela.status == 'Pendente', Parcela.data_vencimento >= hoje, Parcela.data_vencimento <= data_limite).all()
    df = pd.DataFrame([{'Pedido': p.pedido.numero_pedido, 'Cliente': p.pedido.cliente_nome, 'Valor': p.valor, 'Vencimento': p.data_vencimento.strftime('%d/%m/%Y')} for p in parcelas])
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine='openpyxl') as writer: df.to_excel(writer, index=False)
    out.seek(0); return send_file(out, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name='alertas.xlsx')

if __name__ == '__main__':
    with app.app_context(): 
        db.create_all()
        parcelas_orfas = Parcela.query.filter(~Parcela.pedido_id.in_(db.session.query(Pedido.id))).all()
        if parcelas_orfas:
            for p in parcelas_orfas:
                db.session.delete(p)
            db.session.commit()
            print(f"Limpeza de Inicialização completa: {len(parcelas_orfas)} parcelas limpas!")

    app.run(debug=True)