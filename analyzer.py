import asyncio
import aiohttp
from bs4 import BeautifulSoup
import re
import json
import math
import logging
from datetime import datetime, timedelta, timezone # Adicionado timezone
import pandas as pd
import yfinance as yf
from pygooglenews import GoogleNews # Para notícias do Google

logger = logging.getLogger(__name__) # O logger será configurado no app.py

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

class HybridStockAnalyzer:
    def __init__(self, finnhub_client=None):
        self.finnhub_client = finnhub_client
        logger.info(f"HybridStockAnalyzer inicializado. Cliente Finnhub {'CONFIGURADO' if finnhub_client else 'NÃO CONFIGURADO'}.")

    def _prepare_ticker_variants(self, original_ticker: str) -> dict:
        base_ticker = original_ticker.strip().upper()
        
        fundamentus_ticker = base_ticker.replace(".SA", "")
        finnhub_ticker = fundamentus_ticker # Finnhub geralmente usa o base para BR
        
        yfinance_ticker = fundamentus_ticker
        if not yfinance_ticker.endswith(".SA"):
            # Heurística simples para ações BR comuns (terminadas em 3, 4, 5, 6, 11 ou BDRs XXXXX34)
            if re.match(r"^[A-Z]{4}\d{1,2}$", yfinance_ticker): 
                 yfinance_ticker = f"{yfinance_ticker}.SA"
        
        return {
            "original": original_ticker,
            "base": base_ticker, # Ticker mais "limpo" e em maiúsculas (PETR4)
            "yfinance": yfinance_ticker, # Ex: PETR4.SA
            "fundamentus": fundamentus_ticker, # Ex: PETR4
            "finnhub": finnhub_ticker # Ex: PETR4
        }

    def clean_value(self, text_value):
        if text_value is None:
            return 0.0
        text_value_stripped = text_value.strip()
        if text_value_stripped == "-" or text_value_stripped == "":
            return 0.0
        
        original_for_log = text_value_stripped
        cleaned_text = text_value_stripped

        if 'R$' in cleaned_text:
            cleaned_text = cleaned_text.replace('R$', '').strip()

        is_percentage = '%' in cleaned_text
        if is_percentage:
            cleaned_text = cleaned_text.replace('%', '').strip()

        # Lógica de limpeza de pontos e vírgulas:
        # 1. Se tem vírgula e ponto, e o último ponto vem antes da última vírgula (ex: 1.234,56)
        #    -> remover pontos, trocar vírgula por ponto.
        # 2. Se tem vírgula e não tem ponto (ex: 1234,56)
        #    -> trocar vírgula por ponto.
        # 3. Se tem ponto e não tem vírgula (ex: 1234.56 ou 1.234)
        #    -> Se mais de um ponto, remover todos menos o último (assumindo formato americano 1,234.56)
        #       Esta parte é tricky pois Fundamentus usa "." como milhar.
        # A abordagem mais segura para Fundamentus: remover todos os pontos, depois trocar vírgula por ponto.
        
        cleaned_text = cleaned_text.replace('.', '') # Remove separadores de milhar
        cleaned_text = cleaned_text.replace(',', '.') # Converte vírgula decimal para ponto
        
        cleaned_text = re.sub(r'[^\d\.\-]', '', cleaned_text) # Remove outros caracteres não desejados

        if cleaned_text in ['', '-']:
            return 0.0
            
        try:
            value = float(cleaned_text)
            if is_percentage:
                value /= 100.0
            return value
        except ValueError as e:
            logger.warning(f"Erro na conversão de '{original_for_log}' (processado como '{cleaned_text}'): {e}. Retornando 0.0.")
            return 0.0

    def _extract_table_data(self, html_soup, table_title_texts):
        table_data = {}
        if not isinstance(table_title_texts, list):
            table_title_texts = [table_title_texts]

        found_table = None
        title_text_found = None
        for title_text_candidate in table_title_texts:
            try:
                title_span = html_soup.find(lambda tag: tag.name == 'span' and tag.get('class') == ['txt'] and tag.string and tag.string.strip() == title_text_candidate)
                if title_span:
                    current_element = title_span
                    while current_element and current_element.name != 'table':
                        current_element = current_element.parent
                    if current_element and current_element.name == 'table':
                        found_table = current_element
                        title_text_found = title_text_candidate
                        logger.debug(f"Tabela '{title_text_found}' encontrada.")
                        break
            except Exception as e:
                logger.error(f"Erro ao procurar título da tabela '{title_text_candidate}': {e}")
                continue
        
        if found_table:
            rows = found_table.find_all('tr')
            dre_labels_order = [] # Para DRE, manter a ordem e diferenciar 12m de 3m
            is_dre_table = title_text_found in ["Dados demonstrativos de resultados", "Demonstrativo de Resultados"]

            for row_idx, row in enumerate(rows):
                label_cells = row.find_all('td', class_='label')
                data_cells = row.find_all('td', class_='data')

                if not label_cells and not data_cells and row_idx == 0:
                    nivel2_td = row.find('td', class_='nivel2')
                    if nivel2_td and nivel2_td.find('span', class_='txt'):
                        continue 

                for i in range(len(label_cells)):
                    if i < len(data_cells):
                        label_cell = label_cells[i]
                        data_cell = data_cells[i]
                        
                        label_span = label_cell.find('span', class_='txt')
                        data_span = data_cell.find('span', class_='txt')

                        if label_span and data_span and label_span.string and data_span.string:
                            label_orig = label_span.string.strip()
                            value_str = data_span.string.strip()
                            
                            key_to_store = label_orig
                            if is_dre_table and label_orig in ["Receita Líquida", "EBIT", "Lucro Líquido"]:
                                if label_orig not in dre_labels_order:
                                    dre_labels_order.append(label_orig)
                                    key_to_store = f"{label_orig} (Últimos 12 meses)"
                                else: # Já apareceu, então é o de 3 meses
                                    key_to_store = f"{label_orig} (Últimos 3 meses)"
                            
                            if key_to_store not in table_data: # Evita sobrescrever se já pegou (ex: DRE 12m)
                                table_data[key_to_store] = self.clean_value(value_str)
                                logger.debug(f"Extraído de tabela '{title_text_found}': '{key_to_store}' -> {table_data[key_to_store]}")
        else:
            logger.warning(f"Nenhuma tabela encontrada para os títulos: {table_title_texts}")
        return table_data

    def extract_financial_data(self, html_soup, original_ticker_input):
        data = {}
        
        main_indicators_mapping = {
            'Cotação': 'cotacao_atual', 'P/L': 'preco_lucro', 'P/VP': 'preco_valor_patrimonial',
            'P/EBIT': 'preco_ebit', 'PSR': 'price_sales_ratio', 'EV / EBITDA': 'ev_ebitda',
            'Div. Yield': 'dividend_yield', 'LPA': 'lucro_por_acao', 'VPA': 'valor_patrimonial_acao',
            'Marg. Bruta': 'margem_bruta', 'Marg. EBIT': 'margem_ebit', 'Marg. Líquida': 'margem_liquida',
            'ROE': 'roe', 'ROIC': 'roic', 'Liquidez Corr': 'liquidez_corrente',
            'Div Br/ Patrim': 'divida_bruta_patrimonio', 'Cres. Rec (5a)': 'cres_receita_5a',
            'Valor da Firma': 'enterprise_value',
            'Nro. Ações': 'numero_acoes',
            # 'Patrim. Líq': 'patrimonio_liquido_total' # Melhor pegar da tabela de balanço para consistência
        }
        
        all_spans_with_txt_class = html_soup.find_all('span', class_='txt')

        for display_label, key in main_indicators_mapping.items():
            found_span_for_label = None
            for span_tag_candidate in all_spans_with_txt_class:
                span_content = span_tag_candidate.string
                if span_content and span_content.strip() == display_label:
                    parent_td_candidate = span_tag_candidate.find_parent('td')
                    if parent_td_candidate and 'label' in parent_td_candidate.get('class', []):
                        found_span_for_label = span_tag_candidate
                        break 
            
            text_to_convert = None
            if found_span_for_label:
                label_td_found = found_span_for_label.find_parent('td')
                if label_td_found:
                    value_td = label_td_found.find_next_sibling('td', class_='data')
                    if not value_td: value_td = label_td_found.find_next_sibling('td') # Fallback
                    if value_td:
                        value_span = value_td.find('span', class_='txt')
                        if value_span and value_span.string:
                            text_to_convert = value_span.string.strip()
            
            data[key] = self.clean_value(text_to_convert)
            if data[key] == 0.0 and text_to_convert is None:
                 logger.info(f"Indicador principal '{display_label}' (chave: {key}) para {original_ticker_input} não encontrado ou valor vazio.")

        # Extrair dados de tabelas específicas
        dados_dre = self._extract_table_data(html_soup, ["Dados demonstrativos de resultados", "Demonstrativo de Resultados"])
        data['ebit_12m'] = dados_dre.get('EBIT (Últimos 12 meses)', dados_dre.get('EBIT', 0.0))
        data['lucro_liquido_12m'] = dados_dre.get('Lucro Líquido (Últimos 12 meses)', dados_dre.get('Lucro Líquido', 0.0))
        data['receita_liquida_12m'] = dados_dre.get('Receita Líquida (Últimos 12 meses)', dados_dre.get('Receita Líquida', 0.0))
        
        balanco_data = self._extract_table_data(html_soup, ["Dados Balanço Patrimonial", "Balanço Patrimonial"])
        data['ativo_circulante'] = balanco_data.get('Ativo Circulante', 0.0)
        data['passivo_circulante'] = balanco_data.get('Passivo Circulante', 0.0) 
        if data['passivo_circulante'] == 0.0 and data['ativo_circulante'] != 0.0 : # Só avisa se Ativo Circulante foi encontrado
             logger.warning(f"PASSIVO CIRCULANTE não encontrado explicitamente para {original_ticker_input}. NWC para Greenblatt pode ser impreciso. Ativo Circ.: {data['ativo_circulante']}")
        
        # Para Ativo Imobilizado, Fundamentus pode usar "Ativo Imobilizado" ou "Imobilizado"
        data['ativo_imobilizado_liquido'] = balanco_data.get('Ativo Imobilizado', balanco_data.get('Imobilizado', 0.0))
        if data['ativo_imobilizado_liquido'] == 0.0: # Fallback mais genérico
            data['ativo_imobilizado_liquido'] = balanco_data.get('Ativo Não Circulante', 0.0) 
            if data['ativo_imobilizado_liquido'] != 0.0:
                logger.info(f"Usando 'Ativo Não Circulante' como fallback para 'Ativo Imobilizado' para {original_ticker_input}.")

        data['patrimonio_liquido_total'] = balanco_data.get('Patrim. Líq', data.get('patrimonio_liquido_total', 0.0))


        nome_empresa_completo = "N/A"
        empresa_label_span = html_soup.find(lambda tag: tag.name == 'span' and tag.get('class') == ['txt'] and tag.string and tag.string.strip() == 'Empresa')
        if empresa_label_span:
            parent_td = empresa_label_span.find_parent('td')
            if parent_td and 'label' in parent_td.get('class', []):
                value_td = parent_td.find_next_sibling('td', class_='data')
                if value_td:
                    nome_span = value_td.find('span', class_='txt')
                    if nome_span and nome_span.string:
                        nome_empresa_completo = nome_span.string.strip()
        data['nome_empresa_completo'] = nome_empresa_completo
        
        # Greenblatt
        ev_g = data.get('enterprise_value', 0.0)
        ebit_g = data.get('ebit_12m', 0.0) # EBIT dos últimos 12 meses
        nwc_g = data['ativo_circulante'] - data['passivo_circulante']
        nfa_g = data['ativo_imobilizado_liquido']

        data['greenblatt_nwc_calculado'] = nwc_g
        data['greenblatt_nfa_usado'] = nfa_g
        data['greenblatt_ebit_usado'] = ebit_g
        data['greenblatt_ev_usado'] = ev_g
        
        data['greenblatt_earnings_yield'] = (ebit_g / ev_g) if ev_g else 0.0
        capital_investido_g = nwc_g + nfa_g
        data['greenblatt_return_on_capital'] = (ebit_g / capital_investido_g) if capital_investido_g else 0.0
            
        logger.info(f"Greenblatt ({original_ticker_input}) - EV: {ev_g}, EBIT: {ebit_g}, NWC: {nwc_g}, NFA: {nfa_g}")
        logger.info(f"Greenblatt Yield ({original_ticker_input}): {data['greenblatt_earnings_yield']:.4%}, Greenblatt ROC: {data['greenblatt_return_on_capital']:.4%}")

        return data

    def calculate_fair_price(self, data, ticker_input_original): # ticker_input_original para logging
        res = {}
        lpa = data.get('lucro_por_acao', 0.0)
        vpa = data.get('valor_patrimonial_acao', 0.0)
        cotacao = data.get('cotacao_atual', 0.0)
        roe = data.get('roe', 0.0) 
        div_yield = data.get('dividend_yield', 0.0)

        # Graham
        res['graham'] = 0.0
        if lpa > 0 and vpa > 0:
            try:
                graham_value = math.sqrt(22.5 * lpa * vpa) 
                res['graham'] = round(graham_value, 2)
            except ValueError: # lpa ou vpa podem ser mínimos e causar erro com abs() se não forem estritamente > 0
                logger.warning(f"Erro no cálculo de Graham para {ticker_input_original} com LPA={lpa}, VPA={vpa}. Verifique se são positivos.")
        
        # DDM
        res['ddm'] = 0.0
        if cotacao > 0 and div_yield > 0 and roe > 0 and lpa != 0: # lpa != 0 para evitar DivByZero no payout
            dpa = cotacao * div_yield
            payout_ratio = dpa / lpa
            if 0 <= payout_ratio <= 1: 
                retention_ratio = 1 - payout_ratio
                g = roe * retention_ratio 
                required_rate_of_return = 0.12 
                if required_rate_of_return > g and g >= 0:
                    d1 = dpa * (1 + g)
                    denominator_ddm = required_rate_of_return - g
                    if denominator_ddm > 1e-6: # Evitar divisão por zero ou número muito pequeno
                        ddm_value = d1 / denominator_ddm
                        res['ddm'] = round(min(max(ddm_value, 0), cotacao * 5), 2) # Limita o DDM
                    else: logger.warning(f"DDM para {ticker_input_original}: Denominador (rr - g) é zero ou muito pequeno.")
                else: logger.debug(f"DDM para {ticker_input_original}: Condições (rr > g >=0) não atendidas. rr={required_rate_of_return}, g={g}")
            else: logger.debug(f"DDM para {ticker_input_original}: Payout ratio ({payout_ratio:.2f}) fora do intervalo [0,1].")
        else: logger.debug(f"DDM para {ticker_input_original}: Dados insuficientes/inválidos. Cotação={cotacao}, Yield={div_yield}, ROE={roe}, LPA={lpa}")

        # P/L Ajustado
        score_pl = 0
        if roe > 0.15: score_pl += 3
        elif roe > 0.10: score_pl += 2
        if data.get('liquidez_corrente', 0.0) > 1.5: score_pl += 2
        pl_justo_multiplicador = min(8 + score_pl * 1.5, 25) 
        res['pe_adjusted'] = round(lpa * pl_justo_multiplicador, 2)

        # P/VP Ajustado
        multiplicador_pvp = 1.0
        if roe > 0.20: multiplicador_pvp = 2.5
        elif roe > 0.15: multiplicador_pvp = 2.0
        elif roe > 0.10: multiplicador_pvp = 1.5
        res['pvp_adjusted'] = round(vpa * multiplicador_pvp, 2)
        
        # Média Ponderada (apenas de valores positivos)
        vals, wts = [], []
        for key, weight in [('graham', 0.3), ('ddm', 0.2), ('pe_adjusted', 0.3), ('pvp_adjusted', 0.2)]:
            val = res.get(key, 0.0)
            if val > 0: 
                vals.append(val)
                wts.append(weight)
        res['average'] = 0.0
        if vals:
            total_wt = sum(wts)
            if total_wt > 0:
                 res['average'] = round(sum(v_i * (w_i / total_wt) for v_i, w_i in zip(vals, wts)), 2)
        
        logger.debug(f"Preços Justos para {ticker_input_original}: {res}")
        return res

    def generate_investment_analysis(self, data, fair_prices, ticker_input_original):
        analysis = {
            'recommendation': 'NEUTRO', 'risk_level': 'MÉDIO',
            'strengths': [], 'weaknesses': [], 'summary': '', 'score': 0
        }
        cotacao_atual = data.get('cotacao_atual', 0.0)
        preco_justo_medio = fair_prices.get('average', 0.0)
        score_analise = 0

        if preco_justo_medio > 0 and cotacao_atual > 0:
            discount_margin = (preco_justo_medio - cotacao_atual) / cotacao_atual # Mudança para (PJ-PA)/PA
            
            if discount_margin > 0.30: analysis['recommendation'] = 'COMPRAR FORTE'; score_analise += 3
            elif discount_margin > 0.15: analysis['recommendation'] = 'COMPRAR'; score_analise += 2
            elif discount_margin > 0.05: analysis['recommendation'] = 'COMPRAR FRACO'; score_analise += 1
            elif discount_margin < -0.15: analysis['recommendation'] = 'VENDER FRACO'; score_analise -= 1 # Se preço > PJ
            elif discount_margin < -0.30: analysis['recommendation'] = 'VENDER'; score_analise -= 2
            elif discount_margin < -0.50: analysis['recommendation'] = 'VENDER FORTE'; score_analise -=3
            analysis['strengths'].append(f"Potencial de valorização (vs Cotação Atual): {discount_margin:.2%}")
        elif cotacao_atual <= 0 and preco_justo_medio > 0:
             analysis['recommendation'] = 'ANALISAR (Cotação Anormal)'; 
        elif preco_justo_medio <= 0 and cotacao_atual > 0 :
            analysis['recommendation'] = 'CAUTELA'
            analysis['weaknesses'].append("Média dos preços justos (positivos) não é conclusiva ou é zero.")

        roe = data.get('roe', 0.0)
        roic = data.get('roic', 0.0)
        pl = data.get('preco_lucro', 0.0)
        div_br_patrim = data.get('divida_bruta_patrimonio', 0.0)
        liq_corr = data.get('liquidez_corrente', 0.0)
        cres_receita = data.get('cres_receita_5a', 0.0)

        if roe > 0.20: analysis['strengths'].append(f"ROE Excelente: {roe:.2%}"); score_analise += 2
        elif roe > 0.10: analysis['strengths'].append(f"ROE Bom: {roe:.2%}"); score_analise += 1
        elif roe < 0.0 and roe != 0.0 : analysis['weaknesses'].append(f"ROE Negativo: {roe:.2%}"); score_analise -= 2
        
        if roic > 0.15: analysis['strengths'].append(f"ROIC Excelente: {roic:.2%}"); score_analise += 2
        elif roic > 0.10: analysis['strengths'].append(f"ROIC Bom: {roic:.2%}"); score_analise += 1
        elif roic < 0.0 and roic != 0.0: analysis['weaknesses'].append(f"ROIC Negativo: {roic:.2%}"); score_analise -= 2

        if 0 < pl < 10: analysis['strengths'].append(f"P/L (Preço/Lucro) Baixo: {pl:.2f}"); score_analise += 1
        elif pl < 0: analysis['weaknesses'].append(f"P/L Negativo (Prejuízo): {pl:.2f}"); score_analise -= 2
        
        if 0 <= div_br_patrim < 0.5 : analysis['strengths'].append(f"Endividamento (Dív. Bruta/PL) Baixo: {div_br_patrim:.2f}"); score_analise +=1
        elif div_br_patrim > 1.0 : analysis['weaknesses'].append(f"Endividamento (Dív. Bruta/PL) Alto: {div_br_patrim:.2f}"); score_analise -=1
        elif div_br_patrim < 0: analysis['weaknesses'].append(f"Dív. Bruta/PL Negativo (PL Negativo?): {div_br_patrim:.2f}"); score_analise -=2
        
        if liq_corr > 2.0: analysis['strengths'].append(f"Liquidez Corrente Ótima: {liq_corr:.2f}"); score_analise +=1
        elif liq_corr < 1.0 and liq_corr >=0 : analysis['weaknesses'].append(f"Liquidez Corrente Baixa: {liq_corr:.2f}"); score_analise -=1

        if cres_receita > 0.10 : analysis['strengths'].append(f"Crescimento da Receita (5a) Bom: {cres_receita:.2%}"); score_analise +=1
        elif cres_receita < 0.0 and cres_receita != 0.0: analysis['weaknesses'].append(f"Crescimento da Receita (5a) Negativo: {cres_receita:.2%}"); score_analise -=1

        analysis['score'] = max(min(score_analise, 10), -10)
        analysis['summary'] = f"{ticker_input_original.upper()} ({data.get('nome_empresa_completo', ticker_input_original.upper())}): {analysis['recommendation']} (Score {analysis['score']})"
        return analysis

    async def analyze_stock(self, session: aiohttp.ClientSession, ticker_input: str):
        tickers = self._prepare_ticker_variants(ticker_input)
        ticker_fundamentus = tickers["fundamentus"]
        
        url = f"https://www.fundamentus.com.br/detalhes.php?papel={ticker_fundamentus}"
        logger.info(f"Iniciando análise para {ticker_input} (Fundamentus: {ticker_fundamentus}) em {url}")

        try:
            async with session.get(url, headers=HEADERS, timeout=30) as response:
                response.raise_for_status()
                html_content = await response.text(encoding='iso-8859-1')
            
            soup = BeautifulSoup(html_content, 'html.parser')
            
            if not soup.find('span', class_='txt', string='Papel'):
                logger.error(f"Página para {ticker_fundamentus} não parece ser uma página de detalhes válida do Fundamentus.")
                return {'error': 'Ticker inválido ou página não encontrada no Fundamentus.', 
                        'ticker_input_original': ticker_input, 'ticker': tickers['base']}

            financial_data = self.extract_financial_data(soup, ticker_input)
            
            display_name_for_log = financial_data.get('nome_empresa_completo', ticker_input)
            if financial_data.get('cotacao_atual', 0.0) == 0.0 and (display_name_for_log == 'N/A' or not display_name_for_log) :
                logger.warning(f"Dados financeiros essenciais (cotação, nome) não encontrados para {ticker_input}.")
            
            fair_prices = self.calculate_fair_price(financial_data, ticker_input)
            analysis_summary = self.generate_investment_analysis(financial_data, fair_prices, ticker_input)

            result = {
                'ticker': tickers['base'], 
                'ticker_input_original': ticker_input, # Guardar o que o usuário digitou
                'ticker_yfinance': tickers['yfinance'], # Para uso nas abas
                'nome_empresa': financial_data.get('nome_empresa_completo', tickers['base']),
                'data_extracao_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                'financial_data': financial_data,
                'fair_prices': fair_prices,
                'analysis': analysis_summary,
                'source_url': url
            }
            logger.info(f"Análise para {display_name_for_log} concluída com sucesso.")
            return result

        except aiohttp.ClientResponseError as e:
            logger.error(f"Erro HTTP {e.status} ao acessar {url} para {ticker_input}: {e.message}")
            return {'error': f"Erro HTTP {e.status} ao buscar dados do Fundamentus: {e.message}", 
                    'ticker_input_original': ticker_input, 'ticker': tickers.get('base', ticker_input)}
        except asyncio.TimeoutError:
            logger.error(f"Timeout ao acessar {url} para {ticker_input}")
            return {'error': "Timeout ao buscar dados do Fundamentus.", 
                    'ticker_input_original': ticker_input, 'ticker': tickers.get('base', ticker_input)}
        except Exception as e:
            logger.error(f"Erro inesperado na análise de {ticker_input} (Fundamentus): {e}", exc_info=True)
            return {'error': f"Erro inesperado durante a análise do Fundamentus: {str(e)}", 
                    'ticker_input_original': ticker_input, 'ticker': tickers.get('base', ticker_input)}

    async def get_stock_chart_data(self, ticker_input: str, period="3y", interval="1d"):
        tickers = self._prepare_ticker_variants(ticker_input)
        ticker_yf = tickers["yfinance"]
        ticker_fh = tickers["finnhub"]
        
        logger.info(f"Buscando dados de gráfico para '{ticker_input}' (yfinance: '{ticker_yf}', período: {period})")
        try:
            tkr = yf.Ticker(ticker_yf)
            #MODIFICAÇÃO NA VERSÃO ATUAL NÃO TEM MAIS progress=False
            #hist = tkr.history(period=period, interval=interval, auto_adjust=True, actions=False, progress=False)
            hist = tkr.history(period=period, interval=interval, auto_adjust=True, actions=False)
            if not hist.empty and all(col in hist.columns for col in ['Open', 'High', 'Low', 'Close', 'Volume']):
                logger.info(f"Dados de gráfico para '{ticker_yf}' obtidos via yfinance ({len(hist)} registros).")
                return hist.reset_index()
            logger.warning(f"yfinance não retornou dados de gráfico válidos para '{ticker_yf}' (período: {period}).")
        except Exception as e:
            logger.error(f"Erro ao buscar dados de gráfico com yfinance para '{ticker_yf}': {e}", exc_info=True)
        
        if self.finnhub_client:
            logger.info(f"Tentando Finnhub para dados de gráfico para '{ticker_fh}' (período: {period})...")
            try:
                now = int(datetime.now().timestamp())
                days_map = {"2y": 2*365, "1y": 365, "6mo": 180, "1mo": 30, "5d": 5, "1d":1 }
                start_ts = now - (days_map.get(period, 365) * 24 * 60 * 60) # Default 1 ano

                resolution_map = {"1d": "D", "1wk": "W", "1mo": "M"}
                fh_resolution = resolution_map.get(interval, "D")
                
                res_fh = self.finnhub_client.stock_candles(ticker_fh, fh_resolution, start_ts, now)
                if res_fh and res_fh.get('s') == 'ok' and res_fh.get('c') and len(res_fh.get('c',[])) > 0:
                    df_fh = pd.DataFrame(res_fh)
                    # Verificar se as colunas esperadas existem após criar o DataFrame
                    if not all(col in df_fh.columns for col in ['t', 'o', 'h', 'l', 'c', 'v']):
                        logger.warning(f"Finnhub retornou dados para '{ticker_fh}' mas faltam colunas essenciais. Colunas: {df_fh.columns}")
                        return None

                    df_fh['Date'] = pd.to_datetime(df_fh['t'], unit='s')
                    df_fh.rename(columns={'o': 'Open', 'h': 'High', 'l': 'Low', 'c': 'Close', 'v': 'Volume'}, inplace=True)
                    logger.info(f"Dados de gráfico para '{ticker_fh}' obtidos via Finnhub ({len(df_fh)} registros).")
                    return df_fh[['Date', 'Open', 'High', 'Low', 'Close', 'Volume']]
                logger.warning(f"Finnhub não retornou dados de gráfico válidos para '{ticker_fh}'. Status: {res_fh.get('s') if isinstance(res_fh, dict) else 'No/Invalid response'}")
            except Exception as e:
                logger.error(f"Erro ao buscar dados de gráfico com Finnhub para '{ticker_fh}': {e}", exc_info=True)
        logger.warning(f"Não foi possível obter dados de gráfico para '{ticker_input}' de nenhuma fonte.")
        return None

    async def get_company_news(self, ticker_input: str, count=7):
        tickers = self._prepare_ticker_variants(ticker_input)
        search_term_finnhub = tickers["finnhub"] 
        search_term_yfinance = tickers["yfinance"]
        search_term_google = tickers["base"] # Google News pode se beneficiar do ticker base
        nome_empresa_para_google = result_data.get('nome_empresa', '').split(' ')[0] if 'result_data' in locals() and result_data else search_term_google


        news_list = []
        sources_tried = []

        # 1. Finnhub
        if self.finnhub_client:
            sources_tried.append("Finnhub")
            logger.info(f"Buscando notícias para '{search_term_finnhub}' (Finnhub)...")
            try:
                today_dt = datetime.now(timezone.utc) # Use timezone-aware datetime
                from_dt_str = (today_dt - timedelta(days=30)).strftime('%Y-%m-%d')
                to_dt_str = today_dt.strftime('%Y-%m-%d')
                company_news_fh = self.finnhub_client.company_news(search_term_finnhub, _from=from_dt_str, to=to_dt_str)
                if company_news_fh:
                    for news_item_fh in company_news_fh[:count]:
                        if news_item_fh.get('headline') and news_item_fh.get('url'):
                            news_list.append({
                                'source': news_item_fh.get('source', 'Finnhub'),
                                'datetime': news_item_fh.get('datetime'), 
                                'headline': news_item_fh.get('headline'),
                                'summary': news_item_fh.get('summary',''),
                                'url': news_item_fh.get('url')
                            })
                    logger.info(f"{len(news_list)} notícias encontradas para '{search_term_finnhub}' via Finnhub.")
            except Exception as e:
                logger.error(f"Erro ao buscar notícias com Finnhub para '{search_term_finnhub}': {e}", exc_info=True)
        
        # 2. yfinance
        if len(news_list) < count:
            sources_tried.append("yfinance")
            logger.info(f"Tentando yfinance para notícias de '{search_term_yfinance}'...")
            try:
                tkr = yf.Ticker(search_term_yfinance)
                news_items_yf = tkr.news
                if news_items_yf:
                    for item_yf in news_items_yf:
                        if len(news_list) >= count: break
                        if item_yf.get('title') and item_yf.get('link'):
                            news_list.append({
                                'source': item_yf.get('publisher', 'Yahoo Finance'),
                                'datetime': item_yf.get('providerPublishTime'), 
                                'headline': item_yf.get('title'),
                                'summary': item_yf.get('summary', ''), 
                                'url': item_yf.get('link')
                            })
                    logger.info(f"Coletadas/adicionadas notícias para '{search_term_yfinance}' via yfinance. Total agora: {len(news_list)}")
            except Exception as e:
                logger.error(f"Erro ao buscar notícias com yfinance para '{search_term_yfinance}': {e}", exc_info=True)

        # 3. Google News
        if len(news_list) < count:
            sources_tried.append("GoogleNews")
            query_gn = f"{search_term_google} {nome_empresa_para_google} ações" if nome_empresa_para_google != search_term_google else f"{search_term_google} ações"
            logger.info(f"Tentando Google News para '{query_gn}'...")
            try:
                gn = GoogleNews(lang='pt', country='BR')
                search_results_gn = gn.search(query_gn, when='7d')
                
                if search_results_gn and search_results_gn.get('entries'):
                    for entry in search_results_gn['entries']:
                        if len(news_list) >= count: break
                        if entry.get('title') and entry.get('link'):
                            dt_object_gn = None
                            if entry.get('published_parsed'):
                                try: # Convert struct_time to UTC timestamp
                                    dt_gn = datetime(entry.published_parsed[0], entry.published_parsed[1], entry.published_parsed[2],
                                                     entry.published_parsed[3], entry.published_parsed[4], entry.published_parsed[5],
                                                     tzinfo=timezone.utc) # Assume UTC
                                    dt_object_gn = int(dt_gn.timestamp())
                                except: pass 

                            news_list.append({
                                'source': entry.get('source', {}).get('title', 'Google News'),
                                'datetime': dt_object_gn,
                                'headline': entry.get('title'),
                                'summary': entry.get('summary', ''),
                                'url': entry.get('link')
                            })
                    logger.info(f"Coletadas/adicionadas notícias para '{query_gn}' via Google News. Total agora: {len(news_list)}")
            except Exception as e:
                logger.error(f"Erro ao buscar notícias com Google News para '{query_gn}': {e}", exc_info=True)
        
        if not news_list:
            logger.warning(f"Nenhuma notícia encontrada para '{ticker_input}' após tentar: {', '.join(sources_tried)}.")
        
        try:
            news_list_sorted = sorted(
                [n for n in news_list if n.get('datetime') is not None and isinstance(n['datetime'], (int, float))],
                key=lambda x: x['datetime'],
                reverse=True
            )
            news_list_sorted.extend([n for n in news_list if n.get('datetime') is None or not isinstance(n['datetime'], (int, float))])
            news_list = news_list_sorted
        except Exception as e_sort:
            logger.error(f"Erro ao tentar ordenar notícias para '{ticker_input}': {e_sort}")

        return news_list[:count]

    async def get_dividend_history(self, ticker_input: str):
        tickers = self._prepare_ticker_variants(ticker_input)
        ticker_yf = tickers["yfinance"]
        ticker_fh = tickers["finnhub"]
        
        logger.info(f"Buscando histórico de dividendos para '{ticker_yf}' (yfinance)...")
        try:
            tkr = yf.Ticker(ticker_yf)
            dividends_yf = tkr.dividends
            if not dividends_yf.empty:
                df_dividends = dividends_yf.reset_index()
                df_dividends.columns = ['Data', 'Dividendo'] 
                df_dividends['Data'] = pd.to_datetime(df_dividends['Data']).dt.strftime('%Y-%m-%d')
                logger.info(f"Histórico de dividendos para '{ticker_yf}' obtido via yfinance ({len(df_dividends)} registros).")
                return df_dividends.sort_values(by='Data', ascending=False)
            logger.warning(f"yfinance não retornou histórico de dividendos para '{ticker_yf}'.")
        except Exception as e:
            logger.error(f"Erro ao buscar dividendos com yfinance para '{ticker_yf}': {e}", exc_info=True)

        if self.finnhub_client:
            logger.info(f"Tentando Finnhub para dividendos de '{ticker_fh}'...")
            try:
                to_dt_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
                from_dt_str = (datetime.now(timezone.utc) - timedelta(days=10*365)).strftime('%Y-%m-%d')
                dividends_fh_raw = self.finnhub_client.stock_dividends(ticker_fh, _from=from_dt_str, to=to_dt_str)
                if dividends_fh_raw:
                    df_dividends_fh = pd.DataFrame(dividends_fh_raw)
                    if not df_dividends_fh.empty and 'amount' in df_dividends_fh.columns and 'payDate' in df_dividends_fh.columns:
                        # Selecionar e renomear colunas de interesse
                        cols_map = {'payDate': 'Data', 'amount': 'Dividendo', 'currency': 'Moeda', 'exDate': 'Data Ex', 'declarationDate': 'Data Declaração'}
                        # Manter apenas colunas que existem no DataFrame do Finnhub
                        existing_cols_fh = {k_fh: v_std for k_fh, v_std in cols_map.items() if k_fh in df_dividends_fh.columns}
                        df_dividends_fh = df_dividends_fh[list(existing_cols_fh.keys())].rename(columns=existing_cols_fh)
                        
                        if 'Data' in df_dividends_fh.columns: # Assegurar que 'Data' existe após o rename
                            df_dividends_fh['Data'] = pd.to_datetime(df_dividends_fh['Data']).dt.strftime('%Y-%m-%d')
                            df_dividends_fh.sort_values(by='Data', ascending=False, inplace=True)
                            logger.info(f"Histórico de dividendos para '{ticker_fh}' obtido via Finnhub ({len(df_dividends_fh)} registros).")
                            return df_dividends_fh
                logger.warning(f"Finnhub não retornou dados de dividendos válidos para '{ticker_fh}'.")
            except Exception as e:
                logger.error(f"Erro ao buscar dividendos com Finnhub para '{ticker_fh}': {e}", exc_info=True)
        
        logger.warning(f"Não foi possível obter histórico de dividendos para '{ticker_input}' de nenhuma fonte.")
        return None
