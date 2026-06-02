import os
import matplotlib
matplotlib.use('Agg')
import mplfinance as mpf
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'Arial Unicode MS']
plt.rcParams['axes.unicode_minus'] = False

def generate_intraday_chart(stock_ticker, stock_df, benchmark_ticker, qqq_df, filename="intraday_chart.png"):
    # Image generation is temporarily disabled per user request
    return None

def generate_daily_chart(stock_ticker, df, td_list, filename="daily_chart.png"):
    # Image generation is temporarily disabled per user request
    return None