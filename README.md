# Analisador Fundamentalista PRO 📈

## Sobre o Projeto

O Analisador Fundamentalista PRO é uma ferramenta web desenvolvida para auxiliar investidores na análise fundamentalista de ações listadas na bolsa brasileira (B3). A aplicação busca dados de fontes públicas como o site Fundamentus e APIs financeiras (Yahoo Finance, Finnhub) para calcular múltiplos indicadores, aplicar diferentes modelos de valuation e fornecer um resumo analítico.

Este projeto foi concebido e desenvolvido por **Adalberto Brant** com o auxílio da Inteligência Artificial **Gemini Pro do Google**. Nasceu como um script de linha de comando e evoluiu para uma aplicação Streamlit interativa, visando oferecer uma experiência de usuário mais rica e acessível.

O Analisador Fundamentalista PRO faz parte de uma iniciativa maior, sendo construído e aprimorado incrementalmente com novas funcionalidades e fontes de dados.

## Funcionalidades Principais

* Extração de dados fundamentalistas detalhados do site Fundamentus.
* Cálculo de múltiplos de valuation, incluindo:
    * Fórmula de Graham
    * Modelo de Desconto de Dividendos (DDM Simplificado)
    * P/L Ajustado (heurístico)
    * P/VP Ajustado (heurístico)
    * Média Ponderada dos Preços Justos
* Análise das razões da "Fórmula Mágica" de Joel Greenblatt (Earnings Yield e Return on Capital).
* Geração de um score e uma recomendação qualitativa (COMPRAR, VENDER, NEUTRO) baseada na análise.
* Visualização de gráficos diários de cotações (via Yahoo Finance e Finnhub).
* Acesso a notícias e fatos relevantes da empresa (via Finnhub e Yahoo Finance).
* Visualização do histórico de dividendos (via Yahoo Finance e Finnhub).
* Interface web interativa construída com Streamlit.
* Exportação dos dados da análise em formato JSON.
* Logging de atividades do usuário e da aplicação.

## Tecnologias Utilizadas

* **Python 3**
* **Streamlit**: Para a interface web.
* **aiohttp**: Para requisições HTTP assíncronas ao Fundamentus.
* **BeautifulSoup4**: Para parsing do HTML.
* **yfinance**: Para dados históricos de cotações, notícias e dividendos.
* **finnhub-python**: Para dados de mercado (cotações, notícias, dividendos) como fonte primária ou redundância.
* **Pandas**: Para manipulação de dados tabulares.
* **Plotly**: Para gráficos interativos.

## Como Usar

1.  **Clone o Repositório:**
    ```bash
    git clone https://github.com/adalbertobrant/fundamentalistapro.git
    cd fundamentalistapro
    ```

2.  **Crie e Ative um Ambiente Virtual (Recomendado):**
    ```bash
    python -m venv venv
    # Windows
    venv\Scripts\activate
    # Linux/macOS
    source venv/bin/activate
    ```

3.  **Instale as Dependências:**
    ```bash
    pip install -r requirements.txt
    ```

4.  **Configure a Chave API do Finnhub (Opcional, mas recomendado para funcionalidades completas):**
    * Obtenha uma chave API gratuita em [https://finnhub.io/](https://finnhub.io/).
    * **Para rodar localmente:** Você pode editar o arquivo `app.py` e substituir o placeholder da chave Finnhub pela sua chave, ou criar um arquivo `.streamlit/secrets.toml` na raiz do projeto com o seguinte conteúdo:
        ```toml
        FINNHUB_API_KEY = "SUA_CHAVE_API_FINNHUB_AQUI"
        ```
    * **Para deploy no Streamlit Community Cloud:** Configure a chave como um "Secret" nas configurações do seu app com o nome `FINNHUB_API_KEY`.

5.  **Execute o Aplicativo Streamlit:**
    ```bash
    streamlit run app.py
    ```

6.  Abra seu navegador e acesse o endereço fornecido (geralmente `http://localhost:8501`).
7.  Digite o ticker da ação desejada (ex: `PETR4`, `VALE3.SA`) e clique em "Analisar Ação".

## Licença

Este projeto é distribuído sob uma licença de código aberto permissiva,o uso é gratuito para todos.

**Ao utilizar, modificar ou distribuir este código, por favor:**
1.  **Notifique o desenvolvedor original**: Adalberto Brant (github.com/adalbertobrant).
2.  **Referencie o desenvolvedor original e o uso da IA Gemini Pro** em qualquer trabalho derivado ou documentação. Exemplo: "Este projeto/funcionalidade foi baseado no Analisador Fundamentalista PRO de Adalberto Brant, desenvolvido com o auxílio da IA Gemini Pro."

Acreditamos no poder da comunidade e no conhecimento compartilhado!

## Contribuições

Contribuições são bem-vindas! Sinta-se à vontade para abrir *issues* para relatar bugs ou sugerir novas funcionalidades. *Pull requests* também serão avaliados.

## Disclaimer

As informações e análises fornecidas por esta ferramenta são estritamente para fins educacionais e informativos. Não constituem, de forma alguma, aconselhamento financeiro, jurídico ou de investimento. O desenvolvedor e os contribuidores não se responsabilizam por quaisquer perdas ou danos resultantes do uso das informações aqui contidas. Sempre realize sua própria pesquisa e consulte um profissional qualificado antes de tomar decisões de investimento.
