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
    try:
        latest_date = stock_df.index[-1].date()
        s_df = stock_df[stock_df.index.date == latest_date].copy()
        q_df = qqq_df[qqq_df.index.date == latest_date].copy()
        
        common_idx = s_df.index.intersection(q_df.index)
        if len(common_idx) == 0:
            return None
            
        s_df = s_df.loc[common_idx]
        q_df = q_df.loc[common_idx]
        
        mc = mpf.make_marketcolors(up='r', down='g', edge='inherit', wick='inherit', volume='inherit')
        s = mpf.make_mpf_style(marketcolors=mc, gridstyle=':', y_on_right=True)
        
        q_hod = q_df['High'].iloc[0]
        q_lod = q_df['Low'].iloc[0]
        s_hod = s_df['High'].iloc[0]
        s_lod = s_df['Low'].iloc[0]
        
        q_markers_high = [np.nan] * len(q_df)
        q_markers_low = [np.nan] * len(q_df)
        s_markers_high = [np.nan] * len(s_df)
        s_markers_low = [np.nan] * len(s_df)
        
        annotations = []
        
        for i in range(1, len(q_df)):
            q_high = q_df['High'].iloc[i]
            q_low = q_df['Low'].iloc[i]
            s_high = s_df['High'].iloc[i]
            s_low = s_df['Low'].iloc[i]
            t = q_df.index[i].strftime('%H:%M')
            
            is_q_new_high = q_high > q_hod
            is_q_new_low = q_low < q_lod
            
            if is_q_new_high:
                q_hod = q_high
                q_markers_high[i] = q_high + (q_df['High'].max() - q_df['Low'].min()) * 0.02
                annotations.append((i, 1, q_high, f"{t}\nHOD", 'red', 'bottom'))
                
                is_s_new_high = s_high > s_hod
                if is_s_new_high:
                    text = f"{t}\n強:過高"
                    color = 'red'
                else:
                    text = f"{t}\n弱:未過高"
                    color = 'gray'
                s_markers_high[i] = s_high + (s_df['High'].max() - s_df['Low'].min()) * 0.02
                annotations.append((i, 0, s_high, text, color, 'bottom'))
                
            if is_q_new_low:
                q_lod = q_low
                q_markers_low[i] = q_low - (q_df['High'].max() - q_df['Low'].min()) * 0.02
                annotations.append((i, 1, q_low, f"{t}\nLOD", 'green', 'top'))
                
                is_s_new_low = s_low < s_lod
                if is_s_new_low:
                    text = f"{t}\n弱:破底"
                    color = 'green'
                else:
                    text = f"{t}\n強:沒破底"
                    color = 'orange'
                s_markers_low[i] = s_low - (s_df['High'].max() - s_df['Low'].min()) * 0.02
                annotations.append((i, 0, s_low, text, color, 'top'))
                
            s_hod = max(s_hod, s_high)
            s_lod = min(s_lod, s_low)

        ap = []
        ap.append(mpf.make_addplot(q_df, type='candle', panel=1, ylabel=benchmark_ticker))
        
        if pd.Series(q_markers_high).notna().any():
            ap.append(mpf.make_addplot(q_markers_high, type='scatter', markersize=50, marker='v', color='red', panel=1))
        if pd.Series(q_markers_low).notna().any():
            ap.append(mpf.make_addplot(q_markers_low, type='scatter', markersize=50, marker='^', color='green', panel=1))
        if pd.Series(s_markers_high).notna().any():
            ap.append(mpf.make_addplot(s_markers_high, type='scatter', markersize=50, marker='v', color='red', panel=0))
        if pd.Series(s_markers_low).notna().any():
            ap.append(mpf.make_addplot(s_markers_low, type='scatter', markersize=50, marker='^', color='green', panel=0))
        
        fig, axes = mpf.plot(s_df, type='candle', addplot=ap, style=s,
                 title=f"\n{stock_ticker} vs {benchmark_ticker} (3m K-line) - {latest_date}",
                 figratio=(10, 8), figscale=1.2,
                 panel_ratios=(1, 1),
                 returnfig=True)
                 
        ax_stock = axes[0]
        ax_bench = axes[2]
        
        s_y_range = s_df['High'].max() - s_df['Low'].min()
        q_y_range = q_df['High'].max() - q_df['Low'].min()
        
        for idx, panel, price, text, color, va in annotations:
            if panel == 0:
                y_pos = price + s_y_range * 0.05 if va == 'bottom' else price - s_y_range * 0.05
                ax_stock.text(idx, y_pos, text, color=color, ha='center', va=va, fontsize=9, fontweight='bold', alpha=0.8, fontfamily='Microsoft YaHei')
            else:
                y_pos = price + q_y_range * 0.05 if va == 'bottom' else price - q_y_range * 0.05
                ax_bench.text(idx, y_pos, text, color=color, ha='center', va=va, fontsize=9, fontweight='bold', alpha=0.8, fontfamily='Microsoft YaHei')
                 
        fig.savefig(filename, bbox_inches='tight')
        return filename
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Chart error: {e}")
        return None

def generate_daily_chart(stock_ticker, df, td_list, filename="daily_chart.png"):
    try:
        plot_len = min(60, len(df))
        plot_df = df.iloc[-plot_len:].copy()
        plot_td = td_list[-plot_len:]
        
        for w in [10, 20, 60, 200]:
            if f"{w}MA" not in df.columns:
                df[f"{w}MA"] = df['Close'].rolling(window=w).mean()
            plot_df[f"{w}MA"] = df[f"{w}MA"].iloc[-plot_len:]
            
        mav_to_plot = []
        for w in [10, 20, 60, 200]:
            if plot_df[f"{w}MA"].dropna().shape[0] > 0 and plot_df[f"{w}MA"].iloc[-1] > 0:
                mav_to_plot.append(w)
        
        mc = mpf.make_marketcolors(up='r', down='g', edge='inherit', wick='inherit')
        s = mpf.make_mpf_style(marketcolors=mc, gridstyle=':', y_on_right=True)
        
        y_range = plot_df['High'].max() - plot_df['Low'].min()
        offset = y_range * 0.03
        
        fig, axes = mpf.plot(plot_df, type='candle', 
                             mav=tuple(mav_to_plot) if mav_to_plot else None,
                             style=s,
                             title=f"\n{stock_ticker} Daily (MA & TD9)",
                             figratio=(12, 8), figscale=1.2, returnfig=True)
                             
        ax = axes[0]
        
        for i in range(len(plot_df)):
            td = plot_td[i]
            if td != 0:
                if td > 0:
                    ax.text(i, plot_df['High'].iloc[i] + offset, str(td), 
                            color='red', ha='center', va='bottom', fontsize=9, fontweight='bold')
                else:
                    ax.text(i, plot_df['Low'].iloc[i] - offset, str(abs(td)), 
                            color='green', ha='center', va='top', fontsize=9, fontweight='bold')
                            
        fig.savefig(filename, bbox_inches='tight')
        return filename
    except Exception as e:
        print(f"Daily chart error: {e}")
        return None