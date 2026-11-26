# Disclaimer

Last updated: 25 November 2026. Please read this before you use the app, the guide or any Breaking Money content about it.

**Who we are.** Breaking Money and the contributors are not registered with SEBI as an investment adviser, research analyst, stock broker or in any other role. SEBI has not reviewed, approved or endorsed this project.

**Free software, not a service.** This is open-source software under the MIT licence. Each person downloads it and runs it on their own computer, with their own Claude account and their own market data. Breaking Money hosts no trading platform, holds no accounts, and charges nothing for the software. There are no fees, prizes, contests or leagues.

**What our courses are.** Breaking Money's paid courses teach how to build and test AI agents, skills and strategies. They do not provide stock tips, buy or sell signals, research reports, ready-made strategies for real trading, portfolio advice, or claims of returns, and nothing in them should be read that way.

**Real trading is outside this project.** The software cannot connect to a broker. If you choose to trade real money with an algorithm, use only your own SEBI-registered broker and its approved route for retail algorithmic trading, and follow SEBI's framework for it. That decision and its risks are yours.

**Education only, not investment advice.** The app, the code, the guide, its results pages and Breaking Money videos teach how AI agents can be built and controlled. Nothing in them is investment, trading, tax or legal advice, a research report, or a recommendation to buy, sell or hold any security. BUY, SELL, SHORT, COVER and HOLD are outputs of a simulation running on your own computer. They are not tips, signals or calls from Breaking Money. Do not act on them with real money.

**No personal advice.** Paying for a subscription buys access to education and software help. It does not buy advice on your money, and we do not manage, accept or hold anyone's funds.

**Paper trading only.** The app simulates trades with virtual money. It has no code that can place a real order, it blocks broker order URLs and trading SDKs, and it refuses to start if `paper_only` is turned off. There is no prize, league, contest, entry fee or monetary reward.

**SEBI's advisory on paper trading.** In [PR No. 27/2024](https://www.sebi.gov.in/sebi_data/attachdocs/nov-2024/1730717924773.pdf) (4 November 2024) SEBI said that apps and platforms offering virtual trading or paper trading to the public, based on stock price data of listed companies, are in violation of the Securities Contracts (Regulation) Act, 1956 and the SEBI Act, 1992. It advised the public to deal only with registered intermediaries. It also said that for disputes about such activity, SEBI's protection including SCORES, the exchanges' grievance process and online dispute resolution (smartodr.in) will not be available. Read the advisory and decide for yourself before using this software. If you are unsure, do not use it.

**Indian prices are delayed.** SEBI circular [HO/47/17/12(11)2025-MRD-POD3/I/11107/2026](https://www.sebi.gov.in/legal/circulars/may-2026/norms-for-sharing-and-usage-of-price-data-for-educational-purposes_101293.html) (8 May 2026, in force from 1 July 2026) sets a lag of 30 days for sharing and using price data for education. For Indian stocks this app withholds the last 30 days of prices and has no live session. The delay is fixed in `arena/compliance.py` and is not a setting. US sessions use current prices. Neither fact means that SEBI has cleared the app.

**Simulated results are not real results.** Fills, slippage and charges are modelled. Past or simulated performance does not predict future returns, and no return is promised or guaranteed. Results are shown to explain how the agents behave, never as evidence of what anyone can earn.

**Market risk.** Investments in the securities market are subject to market risks. Read all the related documents carefully before investing. Short selling can lose more than the amount invested. Speak to a SEBI-registered adviser before investing, and check registration at [sebi.gov.in](https://www.sebi.gov.in/sebiweb/other/OtherAction.do?doRecognised=yes).

**US stocks.** Residents of India who invest abroad with real money must follow RBI's foreign exchange rules and tax law. This app does not help you do that.

**AI can be wrong.** Claude's answers and the skills' findings can be incorrect, incomplete or out of date. You are responsible for checking them. Third-party skills, the names of their authors, Claude and Anthropic are mentioned to credit them. None of them endorses Breaking Money.

**Market data.** The default feed uses Yahoo Finance through the unofficial `yfinance` library, for personal and educational use. Prices may be delayed or wrong. If you use another feed, you are responsible for following its terms of use. Do not redistribute the data.

**Your account, your cost.** Claude runs on your own account, never a shared one. The spending lock is on by default and refuses calls your Claude plan does not cover. If you turn it off or use an API key, that usage is billed to you. Keep your `.env` file private.

**Not legal advice.** This page describes rules as we understand them on the date above. Rules change, and nothing here certifies that any use is lawful. Ask a securities lawyer about your own situation.

Use this software at your own risk. It is provided "as is", without warranty of any kind (see [LICENSE](LICENSE)).
