# smart_money.py
import sqlite3
from datetime import datetime, date
import io
import os

import pandas as pd
import streamlit as st
import plotly.express as px
from fpdf import FPDF
import matplotlib.pyplot as plt  # hanya untuk export PDF chart

DB_PATH = "finance.db"

# ---------- Database ----------
def init_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS incomes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        amount REAL NOT NULL,
        source TEXT,
        the_date TEXT NOT NULL
    )
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS expenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        amount REAL NOT NULL,
        category TEXT,
        description TEXT,
        the_date TEXT NOT NULL
    )
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS targets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        target_amount REAL,
        target_date TEXT,
        created_at TEXT
    )
    """)
    conn.commit()
    return conn

conn = init_db()

# ---------- Helpers ----------
def to_iso(d):
    if isinstance(d, (date, datetime)):
        return d.isoformat()
    return d

def safe_rerun():
    # kompatibel dengan berbagai versi streamlit
    try:
        st.rerun()
    except Exception:
        try:
            st.experimental_rerun()
        except Exception:
            pass

def add_income(amount, source, the_date):
    c = conn.cursor()
    c.execute("INSERT INTO incomes (amount, source, the_date) VALUES (?, ?, ?)",
              (float(amount), source, to_iso(the_date)))
    conn.commit()

def add_expense(amount, category, description, the_date):
    c = conn.cursor()
    c.execute("INSERT INTO expenses (amount, category, description, the_date) VALUES (?, ?, ?, ?)",
              (float(amount), category, description, to_iso(the_date)))
    conn.commit()

def add_target(name, amount, target_date):
    c = conn.cursor()
    c.execute("INSERT INTO targets (name, target_amount, target_date, created_at) VALUES (?, ?, ?, ?)",
              (name, float(amount), to_iso(target_date), datetime.now().isoformat()))
    conn.commit()

def delete_target(target_id):
    c = conn.cursor()
    c.execute("DELETE FROM targets WHERE id=?", (int(target_id),))
    conn.commit()

def query_df(table, where_clause="", params=()):
    q = f"SELECT * FROM {table} {where_clause}"
    df = pd.read_sql_query(q, conn)
    # convert any column that contains 'date' to datetime (safe)
    for col in df.columns:
        if 'date' in col:
            df[col] = pd.to_datetime(df[col], errors='coerce')
    return df

def get_all_incomes():
    return query_df("incomes", "ORDER BY the_date DESC")

def get_all_expenses():
    return query_df("expenses", "ORDER BY the_date DESC")

def get_targets():
    return query_df("targets", "ORDER BY created_at DESC")

# ---------- Business logic ----------
def monthly_summary(month_year=None):
    incomes = get_all_incomes()
    expenses = get_all_expenses()
    if month_year:
        incomes = incomes[incomes['the_date'].dt.strftime('%Y-%m') == month_year]
        expenses = expenses[expenses['the_date'].dt.strftime('%Y-%m') == month_year]
    total_in = incomes['amount'].sum() if not incomes.empty else 0.0
    total_out = expenses['amount'].sum() if not expenses.empty else 0.0
    saved = total_in - total_out
    saving_rate = (saved / total_in * 100) if total_in > 0 else 0.0
    return {"income": total_in, "expense": total_out, "saved": saved, "saving_rate": saving_rate}

def compute_progress_against_target(target):
    total_saved = (get_all_incomes()['amount'].sum() - get_all_expenses()['amount'].sum())
    percent = min(100.0, max(0.0, total_saved / target['target_amount'] * 100)) if target['target_amount'] > 0 else 0.0
    return total_saved, percent

def consecutive_saving_months(threshold_rate=10):
    incomes = get_all_incomes().copy()
    expenses = get_all_expenses().copy()
    if incomes.empty and expenses.empty:
        return 0, []
    if 'the_date' in incomes.columns:
        incomes['the_date'] = pd.to_datetime(incomes['the_date'], errors='coerce')
        incomes = incomes.dropna(subset=['the_date'])
        incomes['ym'] = incomes['the_date'].dt.strftime('%Y-%m')
    else:
        incomes['ym'] = pd.Series(dtype=str)
    if 'the_date' in expenses.columns:
        expenses['the_date'] = pd.to_datetime(expenses['the_date'], errors='coerce')
        expenses = expenses.dropna(subset=['the_date'])
        expenses['ym'] = expenses['the_date'].dt.strftime('%Y-%m')
    else:
        expenses['ym'] = pd.Series(dtype=str)
    months = sorted(set(list(incomes['ym'].unique()) + list(expenses['ym'].unique())))
    seq = [(m, monthly_summary(m)['saving_rate']) for m in months]
    consecutive = 0
    for m, rate in reversed(seq):
        if rate >= threshold_rate:
            consecutive += 1
        else:
            break
    return consecutive, seq

def prepare_monthly_df():
    inc = get_all_incomes().copy()
    exp = get_all_expenses().copy()
    if inc.empty:
        inc = pd.DataFrame(columns=['amount', 'the_date'])
    if exp.empty:
        exp = pd.DataFrame(columns=['amount', 'the_date'])
    if 'the_date' in inc.columns:
        inc['the_date'] = pd.to_datetime(inc['the_date'], errors='coerce')
        inc = inc.dropna(subset=['the_date'])
        inc['ym'] = inc['the_date'].dt.to_period('M').astype(str)
    if 'the_date' in exp.columns:
        exp['the_date'] = pd.to_datetime(exp['the_date'], errors='coerce')
        exp = exp.dropna(subset=['the_date'])
        exp['ym'] = exp['the_date'].dt.to_period('M').astype(str)
    inc_month = inc.groupby('ym')['amount'].sum().reset_index().rename(columns={'amount': 'income'})
    exp_month = exp.groupby('ym')['amount'].sum().reset_index().rename(columns={'amount': 'expense'})
    month_df = pd.merge(inc_month, exp_month, on='ym', how='outer').fillna(0)
    month_df = month_df.sort_values('ym').reset_index(drop=True)
    month_df['income'] = month_df['income'].astype(float)
    month_df['expense'] = month_df['expense'].astype(float)
    return month_df

# ---------- UI ----------
st.set_page_config(page_title="Smart Money — Pengatur Keuangan", layout="wide")

# Custom CSS (simple, elegant)
st.markdown("""
    <style>
    .stApp { background: linear-gradient(180deg,#f7fbff 0%, #ffffff 100%); }
    .card { background-color: #ffffff; padding: 12px; border-radius: 10px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); }
    .big-metric { font-size: 18px; font-weight:600; color:#0b3d91; }
    .muted { color: #6b7280; }
    .stButton>button { border-radius: 8px; }
    </style>
""", unsafe_allow_html=True)

st.title("💰 Smart Money — Pengatur Keuangan Pribadi")

# sidebar menu with unique key
menu = st.sidebar.selectbox(
    "Menu",
    ["Dashboard", "Tambah Transaksi", "Riwayat", "Target & Simulasi", "Export & Backup", "Pengaturan"],
    key="menu_sidebar_v1"
)

today = date.today()

# ----- Tambah Transaksi -----
if menu == "Tambah Transaksi":
    st.header("➕ Tambah Transaksi")
    tab = st.tabs(["Pemasukan", "Pengeluaran"])
    with tab[0]:
        with st.form("income_form_v1", clear_on_submit=True):
            inc_amount = st.number_input("Jumlah (Rp)", min_value=0.0, format="%.2f", key="inc_amount_input")
            inc_source = st.text_input("Sumber (gaji/bonus/dll)", value="Gaji", key="inc_source_input")
            inc_date = st.date_input("Tanggal", value=today, key="inc_date_input")
            if st.form_submit_button("Simpan Pemasukan", key="save_income_btn"):
                if inc_amount <= 0:
                    st.error("Masukkan jumlah pemasukan > 0")
                else:
                    add_income(inc_amount, inc_source, inc_date)
                    st.success("Pemasukan tersimpan.")
                    safe_rerun()

    with tab[1]:
        with st.form("expense_form_v1", clear_on_submit=True):
            exp_amount = st.number_input("Jumlah (Rp)", min_value=0.0, format="%.2f", key="exp_amount_input")
            categories = ["Makan", "Transport", "Hiburan", "Tagihan", "Belanja", "Investasi", "Lainnya"]
            exp_category = st.selectbox("Kategori", categories, key="exp_category_input")
            exp_desc = st.text_input("Deskripsi (opsional)", key="exp_desc_input")
            exp_date = st.date_input("Tanggal pengeluaran", value=today, key="exp_date_input")
            if st.form_submit_button("Simpan Pengeluaran", key="save_expense_btn"):
                if exp_amount <= 0:
                    st.error("Masukkan jumlah pengeluaran > 0")
                else:
                    add_expense(exp_amount, exp_category, exp_desc, exp_date)
                    st.success("Pengeluaran tersimpan.")
                    safe_rerun()

# ----- Dashboard -----
elif menu == "Dashboard":
    st.header("📊 Ringkasan Keuangan")
    incomes = get_all_incomes()
    expenses = get_all_expenses()

    total_in = incomes['amount'].sum() if not incomes.empty else 0.0
    total_out = expenses['amount'].sum() if not expenses.empty else 0.0
    total_saved = total_in - total_out

    c1, c2, c3 = st.columns(3)
    c1.markdown(f"<div class='card'><div class='big-metric'>Total Pemasukan</div><div class='muted'>Rp {total_in:,.0f}</div></div>", unsafe_allow_html=True)
    c2.markdown(f"<div class='card'><div class='big-metric'>Total Pengeluaran</div><div class='muted'>Rp {total_out:,.0f}</div></div>", unsafe_allow_html=True)
    c3.markdown(f"<div class='card'><div class='big-metric'>Total Tersimpan</div><div class='muted'>Rp {total_saved:,.0f}</div></div>", unsafe_allow_html=True)

    st.markdown("---")
    st.subheader("📈 Grafik: Tren per Bulan")
    month_df = prepare_monthly_df()
    if month_df.empty:
        st.info("Belum ada data. Tambahkan transaksi terlebih dulu.")
    else:
        fig = px.line(month_df, x="ym", y=["income", "expense"], markers=True,
                      labels={'ym':'Bulan','value':'Jumlah (Rp)','variable':'Tipe'},
                      title="Tren Pemasukan vs Pengeluaran per Bulan")
        st.plotly_chart(fig, use_container_width=True)

    st.subheader("🔍 Pengeluaran per Kategori")
    if not expenses.empty:
        cat_summary = expenses.groupby("category")["amount"].sum().reset_index().sort_values("amount", ascending=False)
        fig2 = px.pie(cat_summary, names="category", values="amount", title="Komposisi Pengeluaran (per kategori)", hole=0.4)
        st.plotly_chart(fig2, use_container_width=True)

        # rekomendasi: kategori boros (>30% pemasukan)
        if total_in > 0:
            over = cat_summary.assign(pct = cat_summary['amount'] / total_in * 100)
            over = over[over['pct'] > 30]
            for _, r in over.iterrows():
                st.warning(f"⚠️ Terlalu besar pengeluaran di **{r['category']}** — {r['pct']:.1f}% dari total pemasukan.")

    st.subheader("📌 Pemasukan per Sumber")
    if not incomes.empty:
        src_summary = incomes.groupby("source")["amount"].sum().reset_index().sort_values("amount", ascending=False)
        fig3 = px.bar(src_summary, x="source", y="amount", title="Pemasukan per Sumber", labels={'amount':'Jumlah (Rp)','source':'Sumber'})
        st.plotly_chart(fig3, use_container_width=True)

    st.markdown("---")
    st.subheader("🎯 Target Ringkasan")
    targets = get_targets()
    if targets.empty:
        st.info("Belum ada target. Buat target di menu 'Target & Simulasi'.")
    else:
        for i, t in targets.iterrows():
            saved, pct = compute_progress_against_target(t)
            pct_int = int(min(100, max(0, round(pct))))
            st.write(f"**{t['name']}** — Target Rp {t['target_amount']:,.0f} — Deadline: {t['target_date'].date() if not pd.isna(t['target_date']) else 'unknown'}")
            st.progress(pct_int)
            st.write(f"Terkumpul: Rp {saved:,.0f} ({pct:.1f}%)")
            if pct >= 100:
                st.success("Target tercapai — selamat!")

    # badge / konsistensi
    st.markdown("---")
    st.subheader("🏅 Konsistensi Hemat")
    threshold = st.slider("Threshold saving rate (%)", 5, 50, 10, key="slider_threshold")
    cons, seq = consecutive_saving_months(threshold)
    st.write(f"Bulan berturut-turut dengan saving rate ≥ {threshold}%: **{cons}**")
    if cons >= 12:
        st.success("Platinum Saver (12 bulan konsisten)")
    elif cons >= 6:
        st.success("Gold Saver (6 bulan konsisten)")
    elif cons >= 3:
        st.info("Silver Saver (3 bulan konsisten)")
    else:
        st.info("Belum dapat badge — terus konsisten menabung")

# ----- Riwayat -----
elif menu == "Riwayat":
    st.header("📜 Riwayat Transaksi")
    tab = st.tabs(["Pemasukan", "Pengeluaran"])
    with tab[0]:
        inc = get_all_incomes()
        if inc.empty:
            st.info("Belum ada pemasukan tercatat.")
        else:
            st.dataframe(inc)
            to_del = st.number_input("ID pemasukan yang ingin dihapus (0 = batal)", min_value=0, step=1, value=0, key="del_inc_input")
            if st.button("Hapus pemasukan", key="del_inc_btn"):
                if to_del > 0:
                    c = conn.cursor()
                    c.execute("DELETE FROM incomes WHERE id=?", (to_del,))
                    conn.commit()
                    st.success("Pemasukan dihapus.")
                    safe_rerun()
    with tab[1]:
        ex = get_all_expenses()
        if ex.empty:
            st.info("Belum ada pengeluaran tercatat.")
        else:
            st.dataframe(ex)
            to_del_e = st.number_input("ID pengeluaran yang ingin dihapus (0 = batal)", min_value=0, step=1, value=0, key="del_exp_input")
            if st.button("Hapus pengeluaran", key="del_exp_btn"):
                if to_del_e > 0:
                    c = conn.cursor()
                    c.execute("DELETE FROM expenses WHERE id=?", (to_del_e,))
                    conn.commit()
                    st.success("Pengeluaran dihapus.")
                    safe_rerun()

# ----- Target & Simulasi -----
elif menu == "Target & Simulasi":
    st.header("🎯 Target & Simulasi")
    with st.form("target_form_v1", clear_on_submit=True):
        tname = st.text_input("Nama Target", key="tname_input")
        tgt_amount = st.number_input("Nominal Target (Rp)", min_value=0.0, format="%.2f", key="tgt_amount_input")
        tgt_date = st.date_input("Target tercapai paling lambat (tanggal)", key="tgt_date_input")
        if st.form_submit_button("Simpan Target", key="save_target_btn"):
            if tname.strip() == "" or tgt_amount <= 0:
                st.error("Isi nama target dan nominal target > 0")
            else:
                add_target(tname, tgt_amount, tgt_date)
                st.success("Target tersimpan.")
                safe_rerun()

    st.markdown("---")
    st.subheader("Daftar Target (hapus menggunakan ID)")
    tlist = get_targets()
    if tlist.empty:
        st.info("Belum ada target.")
    else:
        st.dataframe(tlist)
        to_del_t = st.number_input("ID target yang ingin dihapus (0 = batal)", min_value=0, step=1, value=0, key="del_target_input")
        if st.button("Hapus target", key="del_target_btn"):
            if to_del_t > 0:
                delete_target(to_del_t)
                st.success("Target dihapus.")
                safe_rerun()

    # Simulasi pengurangan pengeluaran
    st.markdown("---")
    st.subheader("Simulasi: Pengaruh Pengurangan Pengeluaran")
    reduction = st.slider("Kurangi pengeluaran sebesar (%)", 0, 50, 10, key="sim_reduction")
    inc_df = get_all_incomes()
    exp_df = get_all_expenses()
    avg_income = inc_df['amount'].sum() / max(1, len(inc_df)) if not inc_df.empty else 0
    avg_expense = exp_df['amount'].sum() / max(1, len(exp_df)) if not exp_df.empty else 0
    st.write(f"Rata-rata pemasukan (per catatan): Rp {avg_income:,.0f}")
    st.write(f"Rata-rata pengeluaran (per catatan): Rp {avg_expense:,.0f}")
    tlist = get_targets()
    if not tlist.empty:
        t0 = tlist.iloc[0]
        saved_now = inc_df['amount'].sum() - exp_df['amount'].sum()
        reduced_expense = avg_expense * (1 - reduction/100.0)
        new_monthly_saving = max(0, avg_income - reduced_expense)
        remaining = max(0, t0['target_amount'] - saved_now)
        months_needed = remaining / new_monthly_saving if new_monthly_saving > 0 else float('inf')
        if months_needed == float('inf'):
            st.warning("Dengan kondisi ini, target tidak tercapai (tidak ada saving setiap bulan).")
        else:
            st.success(f"Jika pengeluaran dikurangi {reduction}%, estimasi tambahan per bulan Rp {new_monthly_saving:,.0f}. Waktu untuk capai target ≈ {months_needed:.1f} bulan.")

# ----- Export & Backup -----
elif menu == "Export & Backup":
    st.header("📦 Export & Backup")
    incomes = get_all_incomes()
    expenses = get_all_expenses()
    targets = get_targets()

    if st.button("Export Semua ke Excel", key="export_excel_btn"):
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            incomes.to_excel(writer, sheet_name='Incomes', index=False)
            expenses.to_excel(writer, sheet_name='Expenses', index=False)
            targets.to_excel(writer, sheet_name='Targets', index=False)
        output.seek(0)
        st.download_button("Download file Excel", data=output.getvalue(), file_name="smart_money_export.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="download_excel_button")

    st.markdown("---")
    if st.button("Export Ringkasan ke PDF", key="export_pdf_btn"):
        month_df = prepare_monthly_df()
        fig_path = "tmp_chart.png"
        if not month_df.empty:
            plt.figure(figsize=(8,4))
            plt.bar(month_df['ym'], month_df['income'], label='income')
            plt.bar(month_df['ym'], month_df['expense'], bottom=month_df['income'], label='expense')
            plt.xticks(rotation=45)
            plt.legend()
            plt.tight_layout()
            plt.savefig(fig_path)
            plt.close()
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Arial", size=12)
        pdf.cell(0, 10, "Smart Money - Ringkasan", ln=True)
        pdf.cell(0, 8, f"Tanggal: {datetime.now().strftime('%Y-%m-%d %H:%M')}", ln=True)
        pdf.ln(4)
        pdf.cell(0, 8, f"Total pemasukan: Rp {incomes['amount'].sum():,.0f}", ln=True)
        pdf.cell(0, 8, f"Total pengeluaran: Rp {expenses['amount'].sum():,.0f}", ln=True)
        pdf.cell(0, 8, f"Total tersimpan: Rp {incomes['amount'].sum() - expenses['amount'].sum():,.0f}", ln=True)
        pdf.ln(6)
        if os.path.exists(fig_path):
            pdf.image(fig_path, x=10, y=None, w=190)
        b = pdf.output(dest='S').encode('latin-1')
        st.download_button("Download PDF Ringkasan", data=b, file_name="smart_money_summary.pdf", mime="application/pdf", key="download_pdf_btn")
        if os.path.exists(fig_path):
            os.remove(fig_path)

    st.markdown("---")
    if st.button("Backup Database", key="backup_db_btn"):
        with open(DB_PATH, "rb") as f:
            data = f.read()
        st.download_button("Download finance.db", data=data, file_name="finance.db", mime="application/octet-stream", key="download_db_btn")

# ----- Pengaturan -----
elif menu == "Pengaturan":
    st.header("⚙️ Pengaturan & Info")
    st.write("File database:", DB_PATH)
    st.write("Dependencies: streamlit, pandas, plotly, openpyxl, fpdf, matplotlib")
    if st.button("Reset semua data (HATI-HATI)", key="reset_db_btn"):
        c = conn.cursor()
        c.execute("DELETE FROM incomes")
        c.execute("DELETE FROM expenses")
        c.execute("DELETE FROM targets")
        conn.commit()
        st.success("Semua data dihapus. Muat ulang aplikasi.")
