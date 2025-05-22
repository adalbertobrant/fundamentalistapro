import streamlit as st
import asyncio
import aiohttp
import pandas as pd
import plotly.graph_objects as go
import logging
from datetime import datetime, timedelta, timezone # Garantir que timezone está importado
import uuid
import os
import json
import hashlib
from pathlib import Path
import time 
import re # Importado para safe_ticker_filename
from bs4 import BeautifulSoup # Para limpar HTML das notícias

# Importar a classe do analyzer
from analyzer import HybridStockAnalyzer 

# --- Configuração de Logging ---
LOG_DIR = Path("logs")
ANALYSIS_DIR = Path("analises")
LOG_DIR.mkdir(parents=True, exist_ok=True)
ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)

log_file_path = LOG_DIR / "streamlit_app.log"
user_activity_log_path = LOG_DIR / "user_activity.log"

# Logger principal do app - configurado para ser pego pelo nome __main__
# e também um logger para o módulo analyzer.
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(name)s - %(module)s - %(funcName)s - %(lineno)d - %(message)s',
    handlers=[
        logging.FileHandler(log_file_path, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__) # Logger específico para este módulo (app.py)
analyzer_logger = logging.getLogger("analyzer") # Para logs do analyzer.py
analyzer_logger.setLevel(logging.INFO) # Logs do analyzer também serão INFO ou mais alto


user_activity_logger = logging.getLogger("user_activity")
user_activity_logger.setLevel(logging.INFO)
user_activity_logger.propagate = False 
if not user_activity_logger.handlers:
    fh_user = logging.FileHandler(user_activity_log_path, encoding='utf-8')
    formatter_user = logging.Formatter('%(asctime)sZ;%(message)s', datefmt='%Y-%m-%dT%H:%M:%S') 
    fh_user.setFormatter(formatter_user)
    user_activity_logger.addHandler(fh_user)
    logging.Formatter.converter = time.gmtime # Configura o logger para usar GMT/UTC para asctime

# --- Cliente Finnhub ---
finnhub_api_key_read = ""
try:
    # Tenta ler dos secrets do Streamlit primeiro (ideal para deploy)
    finnhub_api_key_read = st.secrets.get("FINNHUB_API_KEY", "")
    if finnhub_api_key_read:
        logger.info("Chave API Finnhub lida dos secrets do Streamlit.")
    else: # Se não estiver nos secrets, tenta variáveis de ambiente
        logger.info("Chave API Finnhub não encontrada nos secrets, tentando variáveis de ambiente.")
        finnhub_api_key_read = os.environ.get("FINNHUB_API_KEY", "")
        if finnhub_api_key_read:
            logger.info("Chave API Finnhub lida das variáveis de ambiente.")
        else: # Se não estiver em nenhum dos dois
            logger.warning("Chave API Finnhub não encontrada nos secrets nem nas variáveis de ambiente.")
except AttributeError: # st.secrets não existe (ex: rodando localmente sem secrets.toml configurado para Streamlit)
    logger.info("st.secrets não disponível. Tentando carregar chave Finnhub de variável de ambiente.")
    finnhub_api_key_read = os.environ.get("FINNHUB_API_KEY", "")
    if finnhub_api_key_read:
        logger.info("Chave API Finnhub lida das variáveis de ambiente.")
    else:
        logger.warning("Chave API Finnhub não encontrada em variáveis de ambiente (st.secrets também não disponível).")
except Exception as e: 
    logger.error(f"Erro ao tentar ler st.secrets ou os.environ para FINNHUB_API_KEY: {e}")

finnhub_client_instance = None
# Chave de exemplo pública do Finnhub. É MELHOR O USUÁRIO USAR A SUA PRÓPRIA.
public_finnhub_example_key = "d0db4ghr01qhd59vd3bgd0db4ghr01qhd59vd3c0" 

effective_key = None # Chave que será usada para inicializar o cliente

if finnhub_api_key_read: # Se alguma chave foi lida (de secrets ou env)
    effective_key = finnhub_api_key_read
    if effective_key == public_finnhub_example_key:
        logger.warning(f"A chave API do Finnhub configurada ('{effective_key[:7]}...') é a CHAVE DE EXEMPLO PÚBLICA do Finnhub. "
                       "Esta chave tem limites MUITO restritos e pode não funcionar para todas as requisições ou rapidamente atingir o limite. "
                       "É ALTAMENTE RECOMENDADO que você crie e use sua PRÓPRIA chave API gratuita do Finnhub.")
    # Se não for a de exemplo, ou se for a de exemplo e o usuário insiste em usá-la (pois está configurada)
    # Não precisamos de um log específico aqui, pois o log de inicialização do cliente abaixo já informa.
else: # Nenhuma chave encontrada
    logger.warning("Nenhuma chave API Finnhub foi encontrada (st.secrets, os.environ).")
    # Poderia tentar usar a chave de exemplo pública como último recurso se nenhuma outra foi fornecida.
    # Mas é melhor que o usuário configure explicitamente.
    # Se você quiser que use a de exemplo mesmo se nenhuma for configurada, descomente abaixo:
    # effective_key = public_finnhub_example_key
    # logger.info(f"Nenhuma chave Finnhub configurada, tentando usar a chave de exemplo pública: {effective_key[:7]}...")


if effective_key: # Se temos uma chave para usar (seja ela qual for)
    try:
        import finnhub # Mover import para cá para só importar se formos usar
        finnhub_client_instance = finnhub.Client(api_key=effective_key)
        logger.info(f"Cliente Finnhub inicializado com sucesso (usando chave que inicia com: {effective_key[:7]}...).")
    except Exception as e:
        logger.error(f"Erro ao inicializar cliente Finnhub com a chave ({effective_key[:7]}...): {e}")
        finnhub_client_instance = None 
else:
    logger.warning("Cliente Finnhub NÃO FOI CONFIGURADO pois nenhuma chave API foi fornecida/encontrada. "
                   "Funcionalidades do Finnhub (notícias, fallback de gráfico/dividendos) estarão limitadas.")


# --- Funções Auxiliares para o App ---
def get_session_id():
    if 'session_id' not in st.session_state:
        st.session_state.session_id = str(uuid.uuid4())
        # logger.info(f"Nova sessão Streamlit iniciada: {st.session_state.session_id}") # Logado quando necessário
    return st.session_state.session_id

def get_hashed_session_id():
    session_id = get_session_id()
    return hashlib.sha256(session_id.encode()).hexdigest()[:16]

def get_client_ip_from_streamlit_headers():
    try:
        from streamlit.web.server.server import Server
        session_info = None
        if hasattr(Server.get_current(), '_get_session_info_for_headers'):
             session_info = Server.get_current()._get_session_info_for_headers()
        elif hasattr(Server.get_current(), '_get_session_info'):
             session_info = Server.get_current()._get_session_info()

        if session_info and hasattr(session_info, 'headers') and session_info.headers:
            x_forwarded_for = session_info.headers.get("X-Forwarded-For")
            if x_forwarded_for:
                return x_forwarded_for.split(',')[0].strip()
            x_real_ip = session_info.headers.get("X-Real-Ip")
            if x_real_ip:
                return x_real_ip.strip()
    except Exception as e_ip:
        logger.debug(f"Não foi possível obter IP do cliente via headers: {e_ip}")
    return "N/A"

def log_user_action(session_hash, action, ticker=None, details=""):
    ip_address = get_client_ip_from_streamlit_headers()
    timestamp_unix_gmt = int(datetime.now(timezone.utc).timestamp())

    log_message = f"{timestamp_unix_gmt};{session_hash};{ip_address};{action}"
    if ticker: log_message += f";Ticker:{ticker}"
    if details: log_message += f";Details:{details}"
    user_activity_logger.info(log_message)

# --- Inicializar Analyzer ---
analyzer_instance = HybridStockAnalyzer(finnhub_client=finnhub_client_instance)

# --- Layout do Aplicativo Streamlit ---
st.set_page_config(page_title="Analisador Fundamentalista PRO", layout="wide", initial_sidebar_state="expanded")

st.title("🔍 Analisador Fundamentalista PRO")
st.markdown("Desenvolvido por **Adalberto Brant** com auxílio de **IA Gemini Pro**. Este é um projeto educacional em constante evolução.")
# Adicionar pixel de controle para verificar dados
#st.sidebar.image("https://upload.wikimedia.org/wikipedia/commons/thumb/c/c1/Google_Cloud_logo.svg/2560px-Google_Cloud_logo.svg.png", width=70) 
st.sidebar.markdown("## Configurações da Análise")

st.sidebar.warning(
    "**⚠️ Disclaimer (Aviso Legal):** As informações e análises aqui apresentadas são para fins "
    "educacionais e de estudo, **NÃO constituindo aconselhamento ou recomendação de investimento** "
    "(compra, venda ou manutenção de ativos). O desenvolvedor não se responsabiliza por decisões "
    "de investimento tomadas com base nestes dados. Realize sua própria diligência."
)
st.sidebar.markdown("---")

default_ticker = st.session_state.get('last_ticker', "PETR4")
ticker_input_from_user = st.sidebar.text_input(
    "Digite o Ticker da Ação (ex: PETR4, VALE3):", 
    value=default_ticker,
    help="Para ações brasileiras, apenas o código base (ex: PETR4). O sistema tentará adicionar '.SA' para yfinance."
).strip()

analyze_button = st.sidebar.button("🚀 Analisar Ação", type="primary", use_container_width=True)

# Inicializar estado da sessão para dados das abas
if 'current_analysis_result' not in st.session_state: st.session_state.current_analysis_result = None
if 'current_chart_data' not in st.session_state: st.session_state.current_chart_data = None
if 'current_news_data' not in st.session_state: st.session_state.current_news_data = None
if 'current_dividend_data' not in st.session_state: st.session_state.current_dividend_data = None
# Ticker que foi efetivamente usado para buscar os dados das abas (para evitar re-fetch se o ticker não mudou)
if 'processed_ticker_for_tabs' not in st.session_state: st.session_state.processed_ticker_for_tabs = None


# --- Lógica Principal ---
if analyze_button and ticker_input_from_user:
    st.session_state.last_ticker = ticker_input_from_user 
    session_hash = get_hashed_session_id() # Gera ID de sessão no primeiro uso
    log_user_action(session_hash, "ANALISE_INICIADA", ticker=ticker_input_from_user)
    
    # Limpar dados de análises anteriores antes de uma nova busca
    st.session_state.current_analysis_result = None
    st.session_state.current_chart_data = None
    st.session_state.current_news_data = None
    st.session_state.current_dividend_data = None
    st.session_state.processed_ticker_for_tabs = ticker_input_from_user.upper() # Ticker que está sendo processado

    with st.spinner(f"Buscando e analisando dados para {st.session_state.processed_ticker_for_tabs}, aguarde..."):
        try:
            async def fetch_all_main_data():
                async with aiohttp.ClientSession() as http_session: 
                    return await analyzer_instance.analyze_stock(http_session, ticker_input_from_user) # Passa o input original
            
            # Configurar loop asyncio para Streamlit
            try:
                loop = asyncio.get_event_loop_policy().get_event_loop()
                if loop.is_closed():
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
            except RuntimeError as ex:
                if "There is no current event loop in thread" in str(ex):
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                else: raise

            analysis_result = loop.run_until_complete(fetch_all_main_data())

            if analysis_result and 'error' not in analysis_result:
                st.session_state.current_analysis_result = analysis_result
                
                ticker_base_for_file = analysis_result.get('ticker', ticker_input_from_user.upper())
                safe_ticker_filename = re.sub(r'[^A-Z0-9_]', '', ticker_base_for_file) 
                analysis_file_name = f"{safe_ticker_filename}_{session_hash}.json"
                analysis_file_path = ANALYSIS_DIR / analysis_file_name
                with open(analysis_file_path, 'w', encoding='utf-8') as f:
                    json.dump(analysis_result, f, ensure_ascii=False, indent=4)
                logger.info(f"Análise para {ticker_input_from_user} salva em {analysis_file_path}")
                log_user_action(session_hash, "ANALISE_FUNDAMENTALISTA_CONCLUIDA", ticker=ticker_input_from_user, details=f"File:{analysis_file_name}")
                st.sidebar.success(f"Análise de {ticker_base_for_file} concluída!")
            else:
                error_message = analysis_result.get('error', 'Erro desconhecido na análise.') if isinstance(analysis_result, dict) else "Resultado da análise fundamentalista vazio."
                st.error(f"Falha ao analisar {ticker_input_from_user}: {error_message}")
                logger.error(f"Falha ao analisar (Fundamentus) {ticker_input_from_user}: {error_message}")
                log_user_action(session_hash, "ANALISE_FUNDAMENTALISTA_FALHOU", ticker=ticker_input_from_user, details=error_message)
        except Exception as e:
            st.error(f"Ocorreu um erro inesperado durante a análise de {ticker_input_from_user}: {e}")
            logger.critical(f"Erro crítico ao analisar {ticker_input_from_user} no app: {e}", exc_info=True)
            log_user_action(session_hash, "ANALISE_FUNDAMENTALISTA_ERRO_CRITICO", ticker=ticker_input_from_user, details=str(e)[:100])


# --- Abas de Exibição ---
if st.session_state.current_analysis_result and 'error' not in st.session_state.current_analysis_result:
    result_data = st.session_state.current_analysis_result
    
    # ticker_input_original é o que o usuário digitou, e é o que as funções de API esperam.
    # O analyzer._prepare_ticker_variants fará a adaptação para cada API.
    ticker_for_api_calls = result_data.get('ticker_input_original', 'N/A')
    
    # display_ticker é o ticker base limpo para títulos e logs.
    display_ticker = result_data.get('ticker', 'N/A') 
    nome_empresa_display = result_data.get('nome_empresa', 'N/A')
    
    st.header(f"Resultados para: {display_ticker} - {nome_empresa_display}")

    tab_keys = ["tab_analise_data", "tab_grafico_data", "tab_noticias_data", "tab_dividendos_data"]
    for key in tab_keys:
        if key not in st.session_state:
            st.session_state[key] = {"data": None, "processed_ticker": None}


    tab_analise, tab_grafico, tab_noticias, tab_dividendos = st.tabs([
        "📊 Análise Fundamentalista", 
        "📈 Gráfico de Cotações", 
        "📰 Notícias e Fatos Relevantes", 
        "💰 Dividendos"
    ])

    with tab_analise:
        # ... (código da aba de análise como na sua última versão, sem alterações aqui) ...
        st.subheader("🎯 Resumo da Análise e Recomendações")
        analise_geral = result_data.get('analysis', {})
        col1, col2, col3 = st.columns(3)
        col1.metric("Recomendação", analise_geral.get('recommendation', 'N/A'), help="Baseado na média ponderada dos preços justos e análise de indicadores.")
        col2.metric("Score de Análise", f"{analise_geral.get('score', 0)} / 10", help="Pontuação de -10 a 10 baseada em indicadores e valuation.")
        col3.metric("Nível de Risco Estimado", analise_geral.get('risk_level', 'N/A'), help="Estimativa qualitativa de risco.")
        
        if analise_geral.get('summary'):
            st.info(f"📄 {analise_geral.get('summary')}")

        with st.expander("🔍 Pontos Fortes e Fracos Identificados", expanded=False):
            st.markdown("**👍 Pontos Fortes:**")
            if analise_geral.get('strengths'):
                for item in analise_geral.get('strengths'): st.markdown(f"- {item}")
            else: st.markdown("- *Nenhum ponto forte específico destacado automaticamente.*")
            
            st.markdown("**👎 Pontos Fracos:**")
            if analise_geral.get('weaknesses'):
                for item in analise_geral.get('weaknesses'): st.markdown(f"- {item}")
            else: st.markdown("- *Nenhum ponto fraco específico destacado automaticamente.*")
        
        st.subheader("💰 Preços Justos Calculados (Valuation)")
        fair_prices = result_data.get('fair_prices', {})
        fd_main = result_data.get('financial_data', {})
        cotacao_atual_val = fd_main.get('cotacao_atual', 0.0)
        data_extracao = result_data.get('data_extracao_utc', datetime.now(timezone.utc).isoformat(timespec='seconds'))
        try:
            data_extracao_dt = datetime.fromisoformat(data_extracao.replace("Z", "+00:00"))
            data_extracao_display = data_extracao_dt.strftime('%d/%m/%Y')
        except : 
            data_extracao_display = data_extracao.split('T')[0] if 'T' in data_extracao else data_extracao[:10]

        st.metric("Cotação Atual (Fundamentus)", f"R$ {cotacao_atual_val:.2f}", delta_color="off", help=f"Última cotação registrada pelo Fundamentus em {data_extracao_display}.")

        fp_cols = st.columns(5)
        fp_names = ['Graham', 'DDM', 'P/L Ajustado', 'P/VP Ajustado', 'Média Ponderada']
        fp_keys = ['graham', 'ddm', 'pe_adjusted', 'pvp_adjusted', 'average']
        for i, col_fp in enumerate(fp_cols): 
            if i < len(fp_keys):
                price = fair_prices.get(fp_keys[i], 0.0)
                delta_val = None
                if price > 0 and cotacao_atual_val > 0:
                    delta_val = f"{( (price - cotacao_atual_val) / cotacao_atual_val ):.1%}" 
                col_fp.metric(fp_names[i], f"R$ {price:.2f}", delta=delta_val, help=f"Preço justo ({fp_names[i]}). Delta é o potencial vs cotação atual.")

        st.subheader("🧙‍♂️ Razões da Fórmula Mágica (Greenblatt)")
        col_g1, col_g2 = st.columns(2)
        ey_greenblatt = fd_main.get('greenblatt_earnings_yield', 0.0)
        roc_greenblatt = fd_main.get('greenblatt_return_on_capital', 0.0)
        col_g1.metric("Earnings Yield (EBIT/EV)", f"{ey_greenblatt:.2%}", help="EBIT / Enterprise Value. Quanto maior, melhor.")
        col_g2.metric("Return on Capital (EBIT/(NWC+NFA))", f"{roc_greenblatt:.2%}", help="EBIT / (Capital de Giro Líquido + Ativos Fixos Líquidos). Quanto maior, melhor.")
        nwc_calc_display = f"{fd_main.get('greenblatt_nwc_calculado',0):,.0f}".replace(",", "X").replace(".", ",").replace("X", ".")
        nfa_usado_display = f"{fd_main.get('greenblatt_nfa_usado',0):,.0f}".replace(",", "X").replace(".", ",").replace("X", ".")
        st.caption(f"NWC Calc. (AC-PC): R$ {nwc_calc_display} | NFA Usado (Ativo Imob.): R$ {nfa_usado_display}. Precisão do ROC depende da disponibilidade do Passivo Circulante no Fundamentus.")


        st.subheader("📊 Outros Indicadores Chave")
        ind_cat = {
            "Valor de Mercado e Liquidez": ['cotacao_atual', 'patrimonio_liquido_total', 'numero_acoes', 'enterprise_value', 'liquidez_corrente'],
            "Rentabilidade": ['roe', 'roic', 'margem_bruta', 'margem_ebit', 'margem_liquida'],
            "Endividamento": ['divida_bruta_patrimonio'],
            "Múltiplos de Preço": ['preco_lucro', 'preco_valor_patrimonial', 'preco_ebit', 'price_sales_ratio', 'ev_ebitda'],
            "Crescimento": ['cres_receita_5a']
        }
        for category, keys in ind_cat.items():
            with st.expander(category, expanded= (category == "Valor de Mercado e Liquidez") ):
                cols_per_row = 3
                sub_keys = [keys[i:i + cols_per_row] for i in range(0, len(keys), cols_per_row)]
                for row_keys in sub_keys:
                    item_cols = st.columns(cols_per_row) 
                    for idx, key in enumerate(row_keys):
                        val = fd_main.get(key)
                        display_val = "N/A"
                        label_display = key.replace('_', ' ').title() 
                        if isinstance(val, (int, float)):
                            if any(k_word in key for k_word in ['yield', 'roe', 'roic', 'margem', 'cres']):
                                display_val = f"{val:.2%}"
                            elif val == 0.0 and key not in ['cotacao_atual', 'dividend_yield'] and not (key == 'preco_lucro' and val == 0.0) : # P/L 0 é válido
                                 display_val = "0.00"
                            else: 
                                try: 
                                     display_val = f"{val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if abs(val) >= 1000 else f"{val:.2f}"
                                except ValueError: 
                                     display_val = f"{val:.2f}"
                        item_cols[idx].metric(label_display, display_val)
        
        with st.expander("Dados Financeiros Completos Extraídos (JSON)", expanded=False):
            st.json(result_data.get('financial_data', {}))

    with tab_grafico:
        st.subheader(f"📈 Gráfico de Cotações para {display_ticker}")
        # Usar ticker_for_api_calls (input original do usuário) para buscar dados
        if st.session_state.tab_grafico_data["data"] is None or st.session_state.tab_grafico_data["processed_ticker"] != ticker_for_api_calls:
            with st.spinner(f"Carregando dados do gráfico para {ticker_for_api_calls}..."):
                chart_data_df = asyncio.run(analyzer_instance.get_stock_chart_data(ticker_for_api_calls, period="2y"))
                st.session_state.tab_grafico_data["data"] = chart_data_df
                st.session_state.tab_grafico_data["processed_ticker"] = ticker_for_api_calls
        
        chart_data_df = st.session_state.tab_grafico_data["data"]
        if chart_data_df is not None and not chart_data_df.empty:
            try:
                fig = go.Figure()
                fig.add_trace(go.Candlestick(x=chart_data_df['Date'],
                                   open=chart_data_df['Open'], high=chart_data_df['High'],
                                   low=chart_data_df['Low'], close=chart_data_df['Close'],
                                   name=display_ticker)) # Usar display_ticker no nome do trace
                
                if 'Close' in chart_data_df.columns:
                    chart_data_df['SMA20'] = chart_data_df['Close'].rolling(window=20).mean()
                    chart_data_df['SMA50'] = chart_data_df['Close'].rolling(window=50).mean()
                    fig.add_trace(go.Scatter(x=chart_data_df['Date'], y=chart_data_df['SMA20'], mode='lines', name='SMA 20 Dias', line={'color': 'orange'}))
                    fig.add_trace(go.Scatter(x=chart_data_df['Date'], y=chart_data_df['SMA50'], mode='lines', name='SMA 50 Dias', line={'color': 'yellow'}))

                fig.update_layout(
                    title=f'{display_ticker} Cotações Diárias (Últimos 2 anos)',
                    xaxis_title="Data", yaxis_title="Preço",
                    xaxis_rangeslider_visible=False, template="plotly_dark"
                )
                st.plotly_chart(fig, use_container_width=True)
                log_user_action(get_hashed_session_id(), "GRAFICO_EXIBIDO", ticker=ticker_for_api_calls)
            except Exception as e:
                st.error(f"Erro ao gerar gráfico para {display_ticker}: {e}")
                logger.error(f"Erro ao gerar gráfico para {display_ticker}: {e}", exc_info=True)
        else:
            st.warning(f"Não foi possível carregar os dados do gráfico para {display_ticker}.")

    with tab_noticias:
        st.subheader(f"📰 Notícias e Fatos Relevantes para {display_ticker}")
        if st.session_state.tab_noticias_data["data"] is None or \
           st.session_state.tab_noticias_data["processed_ticker"] != ticker_for_api_calls:
            with st.spinner(f"Carregando notícias para {display_ticker}..."):
                news_items_list = asyncio.run(analyzer_instance.get_company_news(ticker_for_api_calls)) # Passa o input original
                st.session_state.tab_noticias_data["data"] = news_items_list
                st.session_state.tab_noticias_data["processed_ticker"] = ticker_for_api_calls
        
        news_items_list = st.session_state.tab_noticias_data["data"]
        if news_items_list:
            for item in news_items_list:
                news_date_str = "Data não informada"
                if item.get('datetime') and isinstance(item['datetime'], (int, float)):
                    try:
                        news_date = datetime.fromtimestamp(int(item['datetime']), tz=timezone.utc)
                        news_date_str = news_date.strftime('%d/%m/%Y %H:%M %Z')
                    except Exception as e_dt:
                        logger.warning(f"Erro ao formatar data da notícia '{item.get('datetime')}': {e_dt}")
                        news_date_str = str(item.get('datetime')) 
                elif item.get('datetime'): # Se for string, apenas exibe
                     news_date_str = str(item.get('datetime'))

                st.markdown(f"##### [{item.get('headline', 'Sem título')}]({item.get('url', '#')})")
                st.caption(f"Fonte: {item.get('source', 'N/A')} | Data: {news_date_str}")
                
                summary = item.get('summary', '')
                if summary and summary != "N/A": 
                    clean_summary = BeautifulSoup(summary, "html.parser").get_text(separator=" ", strip=True)
                    st.write(f"{clean_summary[:350]}...") 
                st.markdown("---")
            log_user_action(get_hashed_session_id(), "NOTICIAS_EXIBIDAS", ticker=display_ticker, details=f"Count:{len(news_items_list)}")
        else:
            st.warning(f"Nenhuma notícia recente encontrada para {display_ticker} nas fontes consultadas.")
            
    with tab_dividendos:
        st.subheader(f"💰 Histórico de Dividendos para {display_ticker}")
        if st.session_state.tab_dividendos_data["data"] is None or \
           st.session_state.tab_dividendos_data["processed_ticker"] != ticker_for_api_calls:
            with st.spinner(f"Carregando histórico de dividendos para {ticker_for_api_calls}..."):
                dividend_df = asyncio.run(analyzer_instance.get_dividend_history(ticker_for_api_calls))
                st.session_state.tab_dividendos_data["data"] = dividend_df
                st.session_state.tab_dividendos_data["processed_ticker"] = ticker_for_api_calls
        
        dividend_df = st.session_state.tab_dividendos_data["data"]
        if dividend_df is not None and not dividend_df.empty:
            st.markdown("Dividendos ordenados do mais recente para o mais antigo.")
            
            cols_to_display_div = ['Data', 'Dividendo']
            if 'Data Ex' in dividend_df.columns: cols_to_display_div.insert(1, 'Data Ex') 
            if 'Moeda' in dividend_df.columns: cols_to_display_div.append('Moeda')
            
            cols_to_display_actual_div = [col for col in cols_to_display_div if col in dividend_df.columns]
            
            format_dict_div = {"Dividendo": "{:.4f}"} # Sem R$ para ser genérico com moeda
            if 'Moeda' in dividend_df.columns and dividend_df['Moeda'].iloc[0] == 'BRL': # Se for BRL, adiciona R$
                 format_dict_div = {"Dividendo": "R$ {:,.4f}"}


            st.dataframe(dividend_df[cols_to_display_actual_div].style.format(format_dict_div), use_container_width=True)
            log_user_action(get_hashed_session_id(), "DIVIDENDOS_EXIBIDOS", ticker=ticker_for_api_calls, details=f"Count:{len(dividend_df)}")
        else:
            st.warning(f"Não foi possível carregar o histórico de dividendos para {display_ticker}, ou não há registros.")

elif analyze_button and not ticker_input_from_user:
    st.sidebar.error("Por favor, digite um ticker para análise.")
    st.session_state.current_analysis_result = None 

# Rodapé
st.markdown("---")
st.markdown(
    "<div style='text-align: center; font-size: 0.8em; color: #777;'>"
    "Analisador Fundamentalista PRO por Adalberto Brant (com IA Gemini Pro). <br>"
    "Projeto Educacional. Use por sua conta e risco. Verifique todas as informações."
    "</div>", 
    unsafe_allow_html=True
)
