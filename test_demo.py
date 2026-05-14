import pandas as pd
import yfinance as yf
import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from chart_utils import generate_intraday_chart

def main():
    print('Downloading data...')
    qqq = yf.download('QQQ', period='1d', interval='5m')
    nvda = yf.download('NVDA', period='1d', interval='5m')

    if isinstance(qqq.columns, pd.MultiIndex):
        qqq.columns = [c[0] for c in qqq.columns]
    if isinstance(nvda.columns, pd.MultiIndex):
        nvda.columns = [c[0] for c in nvda.columns]

    print('Generating chart...')
    result = generate_intraday_chart('NVDA', nvda, 'QQQ', qqq, 'demo_chart.png')
    print('Chart saved to: ' + str(result))

if __name__ == '__main__':
    main()
