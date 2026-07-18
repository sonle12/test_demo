import math
import os
import urllib

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError


# =========================================================
# 1. CẤU HÌNH ỨNG DỤNG
# =========================================================
st.set_page_config(
    page_title="Phân tích điểm thi THPT 2026",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

TABLE_NAME = "dbo.du_lieu_diem_thi_2026"
ALL_PROVINCES = "Tất cả"

GROUP_ORDER = [
    "Toán > 5 và Ngữ văn > 5",
    "Toán < 5 và Ngữ văn < 5",
    "Toán > 5 và Ngữ văn < 5",
    "Toán < 5 và Ngữ văn > 5",
    "Toán = 5 và Ngữ văn = 5",
    "Toán = 5 và Ngữ văn > 5",
    "Toán = 5 và Ngữ văn < 5",
    "Toán > 5 và Ngữ văn = 5",
    "Toán < 5 và Ngữ văn = 5",
]


# =========================================================
# 2. HÀM ĐỊNH DẠNG
# =========================================================
def format_integer(value) -> str:
    if value is None or pd.isna(value):
        return "0"
    return f"{int(value):,}".replace(",", ".")


def format_decimal(value, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return "Không có dữ liệu"

    formatted = f"{float(value):,.{digits}f}"
    return (
        formatted.replace(",", "TEMP")
        .replace(".", ",")
        .replace("TEMP", ".")
    )


def format_percent(value, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return "0%"
    return f"{format_decimal(value, digits)}%"


def safe_percent(numerator, denominator) -> float:
    if denominator in (None, 0) or pd.isna(denominator):
        return 0.0
    return float(numerator) / float(denominator) * 100


def safe_ratio(numerator, denominator) -> float:
    if denominator in (None, 0) or pd.isna(denominator):
        return 0.0
    return float(numerator) / float(denominator)


# =========================================================
# 3. KẾT NỐI SQL SERVER
# Giữ nguyên logic trong Notebook:
# dotenv -> urllib.parse.quote_plus -> SQLAlchemy engine
# =========================================================
@st.cache_resource
def get_engine():
    load_dotenv(dotenv_path="doten.env")

    raw_conn_str = os.getenv("DB_CONN_STR")
    if not raw_conn_str:
        raise ValueError(
            "Không tìm thấy DB_CONN_STR trong file doten.env. "
            "Hãy kiểm tra lại tên file và biến môi trường."
        )

    params = urllib.parse.quote_plus(raw_conn_str)
    connection_url = f"mssql+pyodbc:///?odbc_connect={params}"

    return create_engine(
        connection_url,
        pool_pre_ping=True,
        pool_recycle=3600,
    )


def build_where(province, extra_conditions=None):
    conditions = []
    params = {}

    if province != ALL_PROVINCES:
        conditions.append("Tinh = :province")
        params["province"] = province

    if extra_conditions:
        if isinstance(extra_conditions, str):
            conditions.append(extra_conditions)
        else:
            conditions.extend(extra_conditions)

    where_sql = ""
    if conditions:
        where_sql = "WHERE " + " AND ".join(conditions)

    return where_sql, params


# =========================================================
# 4. CÁC HÀM ĐỌC DỮ LIỆU
# =========================================================
@st.cache_data(ttl=600, show_spinner=False)
def get_provinces():
    query = text(
        f"""
        SELECT DISTINCT Tinh
        FROM {TABLE_NAME}
        WHERE Tinh IS NOT NULL
        ORDER BY Tinh
        """
    )
    df = pd.read_sql(query, get_engine())
    return df["Tinh"].tolist()


@st.cache_data(ttl=600, show_spinner=False)
def get_summary(province):
    where_sql, params = build_where(province)

    query = text(
        f"""
        SELECT
            COUNT_BIG(*) AS total_candidates,

            SUM(CASE WHEN Toan IS NULL THEN 1 ELSE 0 END)
                AS missing_math,

            SUM(CASE WHEN NguVan IS NULL THEN 1 ELSE 0 END)
                AS missing_literature,

            SUM(
                CASE
                    WHEN Toan IS NULL AND NguVan IS NULL THEN 1
                    ELSE 0
                END
            ) AS missing_both,

            SUM(
                CASE
                    WHEN Toan IS NULL AND NguVan IS NOT NULL THEN 1
                    ELSE 0
                END
            ) AS only_missing_math,

            SUM(
                CASE
                    WHEN Toan IS NOT NULL AND NguVan IS NULL THEN 1
                    ELSE 0
                END
            ) AS only_missing_literature,

            SUM(
                CASE
                    WHEN Toan IS NOT NULL AND NguVan IS NOT NULL THEN 1
                    ELSE 0
                END
            ) AS complete_both,

            COUNT(Toan) AS valid_math,
            COUNT(NguVan) AS valid_literature,

            AVG(CAST(Toan AS FLOAT)) AS mean_math,
            AVG(CAST(NguVan AS FLOAT)) AS mean_literature,

            MIN(CAST(Toan AS FLOAT)) AS min_math,
            MAX(CAST(Toan AS FLOAT)) AS max_math,
            MIN(CAST(NguVan AS FLOAT)) AS min_literature,
            MAX(CAST(NguVan AS FLOAT)) AS max_literature,

            STDEV(CAST(Toan AS FLOAT)) AS std_math,
            STDEV(CAST(NguVan AS FLOAT)) AS std_literature,

            SUM(CASE WHEN Toan < 5 THEN 1 ELSE 0 END)
                AS math_below_5,

            SUM(CASE WHEN NguVan < 5 THEN 1 ELSE 0 END)
                AS literature_below_5

        FROM {TABLE_NAME}
        {where_sql}
        """
    )

    df = pd.read_sql(query, get_engine(), params=params)
    return df.iloc[0].to_dict()


@st.cache_data(ttl=600, show_spinner=False)
def get_quartiles(province, column_name):
    allowed_columns = {"Toan", "NguVan"}
    if column_name not in allowed_columns:
        raise ValueError("Tên cột điểm không hợp lệ.")

    where_sql, params = build_where(
        province,
        f"{column_name} IS NOT NULL",
    )

    query = text(
        f"""
        WITH quartiles AS
        (
            SELECT
                PERCENTILE_CONT(0.25)
                    WITHIN GROUP (ORDER BY CAST({column_name} AS FLOAT))
                    OVER () AS q1,

                PERCENTILE_CONT(0.50)
                    WITHIN GROUP (ORDER BY CAST({column_name} AS FLOAT))
                    OVER () AS median_value,

                PERCENTILE_CONT(0.75)
                    WITHIN GROUP (ORDER BY CAST({column_name} AS FLOAT))
                    OVER () AS q3

            FROM {TABLE_NAME}
            {where_sql}
        )

        SELECT TOP 1
            q1,
            median_value,
            q3
        FROM quartiles
        """
    )

    df = pd.read_sql(query, get_engine(), params=params)
    if df.empty:
        return {"q1": None, "median_value": None, "q3": None}

    result = df.iloc[0].to_dict()

    if pd.notna(result["q1"]) and pd.notna(result["q3"]):
        result["iqr"] = result["q3"] - result["q1"]
        result["lower_bound"] = result["q1"] - 1.5 * result["iqr"]
        result["upper_bound"] = result["q3"] + 1.5 * result["iqr"]
    else:
        result["iqr"] = None
        result["lower_bound"] = None
        result["upper_bound"] = None

    return result


@st.cache_data(ttl=600, show_spinner=False)
def get_distribution(province, column_name):
    allowed_columns = {"Toan", "NguVan"}
    if column_name not in allowed_columns:
        raise ValueError("Tên cột điểm không hợp lệ.")

    where_sql, params = build_where(
        province,
        f"{column_name} IS NOT NULL",
    )

    query = text(
        f"""
        SELECT
            ROUND(CAST({column_name} AS FLOAT) * 4, 0) / 4.0 AS score,
            COUNT_BIG(*) AS candidate_count
        FROM {TABLE_NAME}
        {where_sql}
        GROUP BY ROUND(CAST({column_name} AS FLOAT) * 4, 0) / 4.0
        ORDER BY score
        """
    )

    return pd.read_sql(query, get_engine(), params=params)


@st.cache_data(ttl=600, show_spinner=False)
def get_missing_by_province(province):
    where_sql, params = build_where(province)

    query = text(
        f"""
        SELECT
            Tinh,
            COUNT_BIG(*) AS total_candidates,
            SUM(
                CASE
                    WHEN Toan IS NULL AND NguVan IS NULL THEN 1
                    ELSE 0
                END
            ) AS missing_both
        FROM {TABLE_NAME}
        {where_sql}
        GROUP BY Tinh
        HAVING SUM(
            CASE
                WHEN Toan IS NULL AND NguVan IS NULL THEN 1
                ELSE 0
            END
        ) > 0
        ORDER BY missing_both DESC
        """
    )

    df = pd.read_sql(query, get_engine(), params=params)
    if not df.empty:
        df["missing_rate"] = (
            df["missing_both"] / df["total_candidates"] * 100
        )
    return df


@st.cache_data(ttl=600, show_spinner=False)
def get_score_groups(province):
    where_sql, params = build_where(
        province,
        ["Toan IS NOT NULL", "NguVan IS NOT NULL"],
    )

    query = text(
        f"""
        SELECT
            score_group,
            COUNT_BIG(*) AS candidate_count
        FROM
        (
            SELECT
                CASE
                    WHEN Toan > 5 AND NguVan > 5
                        THEN N'Toán > 5 và Ngữ văn > 5'
                    WHEN Toan < 5 AND NguVan < 5
                        THEN N'Toán < 5 và Ngữ văn < 5'
                    WHEN Toan > 5 AND NguVan < 5
                        THEN N'Toán > 5 và Ngữ văn < 5'
                    WHEN Toan < 5 AND NguVan > 5
                        THEN N'Toán < 5 và Ngữ văn > 5'
                    WHEN Toan = 5 AND NguVan = 5
                        THEN N'Toán = 5 và Ngữ văn = 5'
                    WHEN Toan = 5 AND NguVan > 5
                        THEN N'Toán = 5 và Ngữ văn > 5'
                    WHEN Toan = 5 AND NguVan < 5
                        THEN N'Toán = 5 và Ngữ văn < 5'
                    WHEN Toan > 5 AND NguVan = 5
                        THEN N'Toán > 5 và Ngữ văn = 5'
                    WHEN Toan < 5 AND NguVan = 5
                        THEN N'Toán < 5 và Ngữ văn = 5'
                END AS score_group
            FROM {TABLE_NAME}
            {where_sql}
        ) AS grouped_scores
        GROUP BY score_group
        """
    )

    df = pd.read_sql(query, get_engine(), params=params)
    total = int(df["candidate_count"].sum()) if not df.empty else 0

    complete_index = pd.DataFrame({"score_group": GROUP_ORDER})
    df = complete_index.merge(df, on="score_group", how="left")
    df["candidate_count"] = df["candidate_count"].fillna(0).astype(int)
    df["percentage"] = 0.0

    if total > 0:
        df["percentage"] = df["candidate_count"] / total * 100

    return df


@st.cache_data(ttl=600, show_spinner=False)
def get_pearson(province):
    where_sql, params = build_where(
        province,
        ["Toan IS NOT NULL", "NguVan IS NOT NULL"],
    )

    query = text(
        f"""
        SELECT
            COUNT_BIG(*) AS n,
            SUM(CAST(Toan AS FLOAT)) AS sum_x,
            SUM(CAST(NguVan AS FLOAT)) AS sum_y,
            SUM(CAST(Toan AS FLOAT) * CAST(NguVan AS FLOAT)) AS sum_xy,
            SUM(CAST(Toan AS FLOAT) * CAST(Toan AS FLOAT)) AS sum_x2,
            SUM(CAST(NguVan AS FLOAT) * CAST(NguVan AS FLOAT)) AS sum_y2
        FROM {TABLE_NAME}
        {where_sql}
        """
    )

    values = pd.read_sql(query, get_engine(), params=params).iloc[0]
    n = float(values["n"] or 0)

    if n < 2:
        return None

    numerator = n * values["sum_xy"] - values["sum_x"] * values["sum_y"]
    denominator_x = n * values["sum_x2"] - values["sum_x"] ** 2
    denominator_y = n * values["sum_y2"] - values["sum_y"] ** 2

    denominator = math.sqrt(max(denominator_x, 0) * max(denominator_y, 0))
    if denominator == 0:
        return None

    return float(numerator / denominator)


@st.cache_data(ttl=600, show_spinner=False)
def get_scatter_sample(province, sample_size):
    where_sql, params = build_where(
        province,
        ["Toan IS NOT NULL", "NguVan IS NOT NULL"],
    )

    sample_size = int(sample_size)

    query = text(
        f"""
        SELECT TOP {sample_size}
            CAST(Toan AS FLOAT) AS Toan,
            CAST(NguVan AS FLOAT) AS NguVan
        FROM {TABLE_NAME}
        {where_sql}
        """
    )

    return pd.read_sql(query, get_engine(), params=params)


@st.cache_data(ttl=600, show_spinner=False)
def get_experimental_ranking(province):
    where_sql, params = build_where(
        province,
        ["Toan IS NULL", "TongDiem IS NOT NULL"],
    )

    query = text(
        f"""
        SELECT
            CASE
                WHEN CAST(TongDiem AS FLOAT) / 2.0 < 5
                    THEN N'Yếu'
                WHEN CAST(TongDiem AS FLOAT) / 2.0 BETWEEN 5 AND 6.4
                    THEN N'Trung bình'
                WHEN CAST(TongDiem AS FLOAT) / 2.0 BETWEEN 6.5 AND 7.9
                    THEN N'Khá'
                ELSE N'Giỏi'
            END AS ranking,
            COUNT_BIG(*) AS candidate_count,
            MAX(CAST(TongDiem AS FLOAT) / 2.0) AS max_experimental_score
        FROM {TABLE_NAME}
        {where_sql}
        GROUP BY
            CASE
                WHEN CAST(TongDiem AS FLOAT) / 2.0 < 5
                    THEN N'Yếu'
                WHEN CAST(TongDiem AS FLOAT) / 2.0 BETWEEN 5 AND 6.4
                    THEN N'Trung bình'
                WHEN CAST(TongDiem AS FLOAT) / 2.0 BETWEEN 6.5 AND 7.9
                    THEN N'Khá'
                ELSE N'Giỏi'
            END
        """
    )

    df = pd.read_sql(query, get_engine(), params=params)
    order = ["Yếu", "Trung bình", "Khá", "Giỏi"]
    result = pd.DataFrame({"ranking": order}).merge(
        df,
        on="ranking",
        how="left",
    )
    result["candidate_count"] = result["candidate_count"].fillna(0).astype(int)
    result["max_experimental_score"] = result["max_experimental_score"].fillna(0)

    total = result["candidate_count"].sum()
    result["percentage"] = 0.0
    if total > 0:
        result["percentage"] = result["candidate_count"] / total * 100

    return result


@st.cache_data(ttl=300, show_spinner=False)
def get_data_table(province, row_limit):
    where_sql, params = build_where(province)
    row_limit = int(row_limit)

    query = text(
        f"""
        SELECT TOP {row_limit}
            SBD,
            Nam,
            Tinh,
            Toan,
            NguVan,
            VatLy,
            HoaHoc,
            SinhHoc,
            LichSu,
            DiaLy,
            KinhTePhapLuat,
            TinHoc,
            CongNgheCongNghiep,
            CongNgheNongNghiep,
            NgoaiNgu,
            MaMonNgoaiNgu,
            TongDiem,
            KhoiA,
            KhoiA1,
            KhoiB,
            KhoiC,
            KhoiD
        FROM {TABLE_NAME}
        {where_sql}
        ORDER BY Tinh, SBD
        """
    )

    return pd.read_sql(query, get_engine(), params=params)


# =========================================================
# 5. HÀM VẼ BIỂU ĐỒ
# =========================================================
def create_distribution_chart(
    distribution,
    subject_label,
    mean_value,
    quartiles,
):
    figure = go.Figure()

    figure.add_trace(
        go.Bar(
            x=distribution["score"],
            y=distribution["candidate_count"],
            name=f"Điểm {subject_label}",
            hovertemplate=(
                "Điểm: %{x:.2f}<br>"
                "Số thí sinh: %{y:,}<extra></extra>"
            ),
        )
    )

    reference_lines = [
        (mean_value, "Trung bình", "dash"),
        (quartiles["median_value"], "Trung vị", "dashdot"),
        (quartiles["q1"], "Q1", "dot"),
        (quartiles["q3"], "Q3", "dot"),
    ]

    for value, label, dash_style in reference_lines:
        if value is not None and pd.notna(value):
            figure.add_vline(
                x=float(value),
                line_dash=dash_style,
                annotation_text=f"{label}: {float(value):.2f}",
                annotation_position="top",
            )

    figure.update_layout(
        title=f"Phân bố điểm {subject_label} năm 2026",
        xaxis_title=f"Điểm {subject_label}",
        yaxis_title="Số thí sinh",
        xaxis={"range": [0, 10], "dtick": 0.5},
        bargap=0.03,
        hovermode="x unified",
        height=520,
    )

    return figure


def create_box_plot(
    distribution,
    subject_label,
    mean_value,
    quartiles,
):
    lower_bound = quartiles["lower_bound"]
    upper_bound = quartiles["upper_bound"]

    valid_whiskers = distribution[
        (distribution["score"] >= lower_bound)
        & (distribution["score"] <= upper_bound)
    ]

    lower_whisker = (
        valid_whiskers["score"].min()
        if not valid_whiskers.empty
        else quartiles["q1"]
    )
    upper_whisker = (
        valid_whiskers["score"].max()
        if not valid_whiskers.empty
        else quartiles["q3"]
    )

    outlier_rows = distribution[
        (distribution["score"] < lower_bound)
        | (distribution["score"] > upper_bound)
    ]
    outlier_count = int(outlier_rows["candidate_count"].sum())
    valid_count = int(distribution["candidate_count"].sum())
    outlier_rate = safe_percent(outlier_count, valid_count)

    figure = go.Figure()
    figure.add_trace(
        go.Box(
            q1=[quartiles["q1"]],
            median=[quartiles["median_value"]],
            q3=[quartiles["q3"]],
            lowerfence=[lower_whisker],
            upperfence=[upper_whisker],
            mean=[mean_value],
            y=[subject_label],
            orientation="h",
            name=subject_label,
            boxpoints=False,
            hovertemplate=(
                "Q1: %{q1:.2f}<br>"
                "Trung vị: %{median:.2f}<br>"
                "Q3: %{q3:.2f}<br>"
                "Râu dưới: %{lowerfence:.2f}<br>"
                "Râu trên: %{upperfence:.2f}<extra></extra>"
            ),
        )
    )

    if not outlier_rows.empty:
        figure.add_trace(
            go.Scatter(
                x=outlier_rows["score"],
                y=[subject_label] * len(outlier_rows),
                mode="markers",
                marker={
                    "size": outlier_rows["candidate_count"].clip(6, 18),
                    "opacity": 0.55,
                },
                customdata=outlier_rows["candidate_count"],
                name="Mức điểm ngoại lai",
                hovertemplate=(
                    "Điểm: %{x:.2f}<br>"
                    "Số thí sinh: %{customdata:,}<extra></extra>"
                ),
            )
        )

    figure.add_annotation(
        x=1,
        y=1.16,
        xref="paper",
        yref="paper",
        text=(
            f"Ngoại lai IQR: {format_integer(outlier_count)} "
            f"({format_percent(outlier_rate, 2)})"
        ),
        showarrow=False,
        xanchor="right",
    )

    figure.update_layout(
        title=f"Box plot điểm {subject_label} năm 2026",
        xaxis_title=f"Điểm {subject_label}",
        xaxis={"range": [0, 10], "dtick": 0.5},
        yaxis_title="",
        height=330,
        showlegend=False,
    )

    return figure, outlier_count, outlier_rate


# =========================================================
# 6. SIDEBAR
# =========================================================
try:
    engine = get_engine()
    provinces = get_provinces()
except Exception as error:
    st.error(f"Không thể kết nối SQL Server: {error}")
    st.stop()

st.sidebar.title("🔎 Bộ lọc báo cáo")

selected_province = st.sidebar.selectbox(
    "Lọc theo tỉnh",
    options=[ALL_PROVINCES] + provinces,
)

scatter_sample_size = st.sidebar.slider(
    "Số điểm trên biểu đồ Pearson",
    min_value=1000,
    max_value=10000,
    value=5000,
    step=1000,
)

table_row_limit = st.sidebar.slider(
    "Số dòng trong bảng dữ liệu",
    min_value=100,
    max_value=5000,
    value=1000,
    step=100,
)

st.sidebar.divider()

if selected_province == ALL_PROVINCES:
    report_scope = "toàn quốc"
    st.sidebar.info("Đang hiển thị báo cáo toàn quốc.")
else:
    report_scope = f"mã tỉnh {selected_province}"
    st.sidebar.success(f"Đang lọc theo mã tỉnh {selected_province}.")

if st.sidebar.button("🔄 Làm mới dữ liệu", use_container_width=True):
    st.cache_data.clear()
    st.rerun()

st.sidebar.caption(
    "Các số liệu, đoạn nhận xét và biểu đồ trong nội dung chính "
    "sẽ thay đổi theo bộ lọc tỉnh."
)


# =========================================================
# 7. TẢI CÁC KẾT QUẢ CHÍNH
# =========================================================
try:
    with st.spinner("Đang đọc và tổng hợp dữ liệu từ SQL Server..."):
        summary = get_summary(selected_province)
        math_quartiles = get_quartiles(selected_province, "Toan")
        literature_quartiles = get_quartiles(selected_province, "NguVan")
        math_distribution = get_distribution(selected_province, "Toan")
        literature_distribution = get_distribution(selected_province, "NguVan")
except SQLAlchemyError as error:
    st.error(f"Không thể đọc dữ liệu: {error}")
    st.stop()

if int(summary.get("total_candidates") or 0) == 0:
    st.warning("Không có dữ liệu phù hợp với bộ lọc đã chọn.")
    st.stop()


# =========================================================
# 8. TIÊU ĐỀ VÀ PHẠM VI
# =========================================================
st.title("PHÂN TÍCH ĐIỂM THI THPT NĂM 2026")
st.caption(f"Phạm vi báo cáo hiện tại: **{report_scope}**")

st.header("Mục tiêu, phạm vi và quy ước phân tích")
st.markdown(
    """
Notebook và Dashboard tập trung phân tích kết quả của hai môn bắt buộc là
**Toán** và **Ngữ văn** trong dữ liệu điểm thi THPT năm 2026.

Các nội dung chính gồm:

- kiểm tra cấu trúc và chất lượng dữ liệu;
- thống kê số lượng, tỷ lệ điểm bị thiếu;
- phân tích trung bình, trung vị, tứ phân vị và khoảng IQR;
- kiểm tra ngoại lai theo quy tắc IQR;
- so sánh hai phương án xử lý dữ liệu `NULL`;
- trực quan hóa phân phối điểm Toán và Ngữ văn;
- tính xác suất thực nghiệm thí sinh có điểm dưới 5;
- phân nhóm đồng thời hai môn theo các mức `< 5`, `= 5` và `> 5`;
- đo mức độ liên hệ tuyến tính giữa Toán và Ngữ văn bằng Pearson;
- kiểm tra thử nghiệm giả định liên quan đến biến `TongDiem`.

**Quy ước phân nhóm:** điểm bằng đúng 5 được tách thành nhóm riêng,
không gộp vào nhóm dưới 5 hoặc trên 5. Nhờ đó, các nhóm không bị trùng lặp
và không bỏ sót dữ liệu.

> Các kết quả chỉ phản ánh đặc điểm của tập dữ liệu đang phân tích.
> Không nên sử dụng riêng điểm Toán, Ngữ văn hoặc phép tính thử nghiệm từ
> `TongDiem` để kết luận chính thức một thí sinh đỗ, trượt hay thuộc một
> mức học lực cụ thể.
"""
)


# =========================================================
# 9. PHẦN 1 - KẾT NỐI VÀ CẤU TRÚC
# =========================================================
st.header("1. Kết nối cơ sở dữ liệu và kiểm tra cấu trúc bảng")
st.success(
    "Đã kết nối thành công tới SQL Server bằng "
    "`dotenv → urllib.parse.quote_plus → SQLAlchemy create_engine`."
)
st.code(
    "Bảng được sử dụng: dbo.du_lieu_diem_thi_2026",
    language="text",
)

metric_1, metric_2, metric_3, metric_4 = st.columns(4)
metric_1.metric(
    "Tổng thí sinh",
    format_integer(summary["total_candidates"]),
)
metric_2.metric(
    "Có điểm Toán",
    format_integer(summary["valid_math"]),
)
metric_3.metric(
    "Có điểm Ngữ văn",
    format_integer(summary["valid_literature"]),
)
metric_4.metric(
    "Có đủ cả hai môn",
    format_integer(summary["complete_both"]),
)


# =========================================================
# 10. PHẦN 2 - CHẤT LƯỢNG DỮ LIỆU
# =========================================================
st.header("2. Kiểm tra chất lượng dữ liệu")
st.write(
    "Phần này kiểm tra số điểm bị thiếu và các trường hợp thiếu đồng thời "
    "hai môn bắt buộc. Giá trị `NULL` chỉ thể hiện rằng điểm chưa được ghi "
    "nhận; nó không đồng nghĩa với điểm 0 và chưa đủ để xác định nguyên nhân thiếu."
)

st.subheader("2.1. Kiểm tra số giá trị NULL của môn Toán và Ngữ văn")

quality_1, quality_2, quality_3 = st.columns(3)
quality_1.metric(
    "Thiếu điểm Toán",
    format_integer(summary["missing_math"]),
    delta=format_percent(
        safe_percent(summary["missing_math"], summary["total_candidates"]),
        2,
    ),
    delta_color="inverse",
)
quality_2.metric(
    "Thiếu điểm Ngữ văn",
    format_integer(summary["missing_literature"]),
    delta=format_percent(
        safe_percent(
            summary["missing_literature"],
            summary["total_candidates"],
        ),
        2,
    ),
    delta_color="inverse",
)
quality_3.metric(
    "Thiếu đồng thời hai môn",
    format_integer(summary["missing_both"]),
    delta=format_percent(
        safe_percent(summary["missing_both"], summary["total_candidates"]),
        3,
    ),
    delta_color="inverse",
)

st.subheader("2.2. Các thí sinh thiếu đồng thời điểm Toán và Ngữ văn theo mã tỉnh")

missing_by_province = get_missing_by_province(selected_province)

if missing_by_province.empty:
    st.info("Không có thí sinh thiếu đồng thời cả Toán và Ngữ văn trong phạm vi này.")
else:
    missing_plot = missing_by_province.sort_values("missing_both", ascending=True)
    missing_figure = px.bar(
        missing_plot,
        x="missing_both",
        y=missing_plot["Tinh"].astype(str),
        orientation="h",
        custom_data=["total_candidates", "missing_rate"],
        labels={
            "missing_both": "Số thí sinh thiếu hai môn",
            "y": "Mã tỉnh",
        },
        title="Số thí sinh thiếu đồng thời điểm Toán và Ngữ văn theo mã tỉnh",
    )
    missing_figure.update_traces(
        hovertemplate=(
            "Mã tỉnh: %{y}<br>"
            "Thiếu hai môn: %{x:,}<br>"
            "Tổng thí sinh tỉnh: %{customdata[0]:,}<br>"
            "Tỷ lệ thiếu: %{customdata[1]:.4f}%<extra></extra>"
        )
    )

    average_missing = missing_plot["missing_both"].mean()
    missing_figure.add_vline(
        x=average_missing,
        line_dash="dash",
        annotation_text=f"Trung bình: {average_missing:.2f}",
    )
    missing_figure.update_layout(height=max(420, len(missing_plot) * 28))
    st.plotly_chart(missing_figure, use_container_width=True)

st.subheader("Nhận xét chất lượng dữ liệu")

missing_math_rate = safe_percent(
    summary["missing_math"],
    summary["total_candidates"],
)
missing_literature_rate = safe_percent(
    summary["missing_literature"],
    summary["total_candidates"],
)
complete_rate = safe_percent(
    summary["complete_both"],
    summary["total_candidates"],
)

st.markdown(
    f"""
Trong phạm vi **{report_scope}**, tập dữ liệu có **{format_integer(summary['total_candidates'])} thí sinh**.
Kết quả kiểm tra cho thấy:

- **{format_integer(summary['missing_math'])} thí sinh thiếu điểm Toán**, chiếm khoảng **{format_percent(missing_math_rate, 2)}**;
- **{format_integer(summary['missing_literature'])} thí sinh thiếu điểm Ngữ văn**, chiếm khoảng **{format_percent(missing_literature_rate, 2)}**;
- **{format_integer(summary['missing_both'])} thí sinh thiếu đồng thời cả hai môn**;
- **{format_integer(summary['only_missing_math'])} thí sinh chỉ thiếu Toán** nhưng vẫn có điểm Ngữ văn;
- **{format_integer(summary['only_missing_literature'])} thí sinh chỉ thiếu Ngữ văn** nhưng vẫn có điểm Toán;
- **{format_integer(summary['complete_both'])} thí sinh có đủ cả hai điểm**, chiếm khoảng **{format_percent(complete_rate, 2)}**.

Dữ liệu hiện chỉ thể hiện `NULL`, vì vậy chưa thể kết luận thí sinh vắng thi,
không đăng ký, được miễn thi, bị đình chỉ, bị hủy kết quả hay dữ liệu bị thiếu
khi thu thập. Các bản ghi thiếu nên được loại khỏi phép tính điểm tương ứng
nhưng vẫn phải được báo cáo riêng trong phần chất lượng dữ liệu.

Khi so sánh giữa các tỉnh, cần ưu tiên **tỷ lệ thiếu trên tổng số thí sinh của tỉnh**
thay vì chỉ nhìn vào số lượng tuyệt đối.
"""
)


# =========================================================
# 11. PHẦN 3 - MÔN TOÁN
# =========================================================
st.header("3. Phân tích môn Toán")

st.subheader("3.1. Tứ phân vị, trung vị và khoảng IQR")

math_iqr_metrics = st.columns(6)
math_iqr_metrics[0].metric("Trung bình", format_decimal(summary["mean_math"], 4))
math_iqr_metrics[1].metric("Q1", format_decimal(math_quartiles["q1"], 2))
math_iqr_metrics[2].metric("Trung vị", format_decimal(math_quartiles["median_value"], 2))
math_iqr_metrics[3].metric("Q3", format_decimal(math_quartiles["q3"], 2))
math_iqr_metrics[4].metric("IQR", format_decimal(math_quartiles["iqr"], 2))
math_iqr_metrics[5].metric("Độ lệch chuẩn", format_decimal(summary["std_math"], 4))

st.markdown(
    f"""
Khoảng tứ phân vị của môn Toán được tính bằng:

\\[
IQR=Q3-Q1={format_decimal(math_quartiles['q3'], 2)}-{format_decimal(math_quartiles['q1'], 2)}
={format_decimal(math_quartiles['iqr'], 2)}
\\]

Ngưỡng dưới và ngưỡng trên theo quy tắc IQR lần lượt là
**{format_decimal(math_quartiles['lower_bound'], 3)}** và
**{format_decimal(math_quartiles['upper_bound'], 3)}**.
"""
)

st.subheader("3.2. So sánh hai cách xử lý điểm Toán bị thiếu")

math_imputed_mean = None
if summary["total_candidates"]:
    math_imputed_mean = (
        float(summary["mean_math"] or 0) * int(summary["valid_math"])
        + float(math_quartiles["median_value"] or 0) * int(summary["missing_math"])
    ) / int(summary["total_candidates"])

math_difference = abs(float(summary["mean_math"]) - math_imputed_mean)

st.markdown(
    f"""
Hai phương án được so sánh:

1. thay `NULL` bằng trung vị **{format_decimal(math_quartiles['median_value'], 2)}**;
2. bỏ qua `NULL` và chỉ tính trên các điểm quan sát thực tế.

Khi thay điểm Toán bị thiếu bằng trung vị, điểm trung bình là khoảng
**{format_decimal(math_imputed_mean, 4)}**. Khi bỏ qua `NULL`, điểm trung bình là
**{format_decimal(summary['mean_math'], 4)}**. Mức chênh lệch chỉ khoảng
**{format_decimal(math_difference, 4)} điểm**.

Ảnh hưởng lên trung bình chung nhỏ khi tỷ lệ thiếu thấp và trung vị nằm gần
điểm trung bình. Tuy nhiên, điểm được điền không phải điểm thi thực tế và có thể
làm dữ liệu tập trung hơn. Báo cáo mô tả nên ưu tiên điểm quan sát thật; nếu điền
trung vị để huấn luyện mô hình, nên thêm một cột đánh dấu bản ghi đã được điền.
"""
)

st.subheader("3.3. Biểu đồ phân bố điểm Toán")
st.write(
    "Histogram giúp quan sát hình dạng phân phối. Các đường dọc biểu diễn "
    "trung bình, trung vị, Q1 và Q3. Mỗi khoảng điểm có độ rộng 0,25."
)
math_histogram = create_distribution_chart(
    math_distribution,
    "Toán",
    summary["mean_math"],
    math_quartiles,
)
st.plotly_chart(math_histogram, use_container_width=True)

st.subheader("3.4. Box plot điểm Toán")
math_box, math_outlier_count, math_outlier_rate = create_box_plot(
    math_distribution,
    "Toán",
    summary["mean_math"],
    math_quartiles,
)
st.plotly_chart(math_box, use_container_width=True)

st.subheader("Kết luận môn Toán")
math_mean_minus_median = float(summary["mean_math"]) - float(
    math_quartiles["median_value"]
)

st.markdown(
    f"""
Trong tổng số **{format_integer(summary['total_candidates'])} thí sinh**, có
**{format_integer(summary['valid_math'])} thí sinh có điểm Toán** và
**{format_integer(summary['missing_math'])} thí sinh thiếu điểm Toán**.

Các chỉ số chính gồm:

- điểm trung bình: khoảng **{format_decimal(summary['mean_math'], 4)}**;
- Q1: **{format_decimal(math_quartiles['q1'], 2)}**;
- trung vị: **{format_decimal(math_quartiles['median_value'], 2)}**;
- Q3: **{format_decimal(math_quartiles['q3'], 2)}**;
- IQR: **{format_decimal(math_quartiles['iqr'], 2)}**;
- ngưỡng dưới IQR: **{format_decimal(math_quartiles['lower_bound'], 3)}**;
- ngưỡng trên IQR: **{format_decimal(math_quartiles['upper_bound'], 3)}**.

Khoảng 50% thí sinh có điểm Toán từ **{format_decimal(math_quartiles['q1'], 2)}**
đến **{format_decimal(math_quartiles['q3'], 2)}**. Trung bình chênh trung vị khoảng
**{format_decimal(math_mean_minus_median, 3)} điểm**.

Quy tắc IQR phát hiện **{format_integer(math_outlier_count)} bài thi ngoại lai**,
chiếm khoảng **{format_percent(math_outlier_rate, 2)}** số bài Toán hợp lệ.
Ngoại lai thống kê không đồng nghĩa dữ liệu sai; vẫn cần kiểm tra miền điểm 0–10,
kiểu dữ liệu, số báo danh trùng và các quy tắc nghiệp vụ khác.
"""
)


# =========================================================
# 12. PHẦN 4 - MÔN NGỮ VĂN
# =========================================================
st.header("4. Phân tích môn Ngữ văn")

st.subheader("4.1. So sánh hai cách xử lý điểm Ngữ văn bị thiếu")

literature_imputed_mean = (
    float(summary["mean_literature"] or 0) * int(summary["valid_literature"])
    + float(literature_quartiles["median_value"] or 0)
    * int(summary["missing_literature"])
) / int(summary["total_candidates"])

literature_difference = abs(
    float(summary["mean_literature"]) - literature_imputed_mean
)

st.markdown(
    f"""
Trung vị điểm Ngữ văn là **{format_decimal(literature_quartiles['median_value'], 2)}**.
Khi bỏ qua `NULL`, điểm trung bình là **{format_decimal(summary['mean_literature'], 6)}**;
khi thay `NULL` bằng trung vị, điểm trung bình là
**{format_decimal(literature_imputed_mean, 6)}**.

Mức chênh lệch là **{format_decimal(literature_difference, 6)} điểm**.
Việc điền trung vị thường gần như không làm thay đổi trung bình chung nếu tỷ lệ
thiếu thấp, nhưng có thể làm tăng số quan sát tại đúng mức trung vị và khiến
phương sai hoặc độ lệch chuẩn thấp hơn thực tế.
"""
)

st.subheader("4.2. Biểu đồ phân bố điểm Ngữ văn")
st.write(
    "Biểu đồ được trình bày cùng thang điểm và cách đánh dấu thống kê như "
    "môn Toán để thuận tiện so sánh hai phân phối."
)
literature_histogram = create_distribution_chart(
    literature_distribution,
    "Ngữ văn",
    summary["mean_literature"],
    literature_quartiles,
)
st.plotly_chart(literature_histogram, use_container_width=True)

st.subheader("4.3. Box plot và ngoại lai môn Ngữ văn")
literature_box, literature_outlier_count, literature_outlier_rate = create_box_plot(
    literature_distribution,
    "Ngữ văn",
    summary["mean_literature"],
    literature_quartiles,
)
st.plotly_chart(literature_box, use_container_width=True)

st.subheader("Kết luận môn Ngữ văn")
literature_median_minus_mean = float(
    literature_quartiles["median_value"]
) - float(summary["mean_literature"])

st.markdown(
    f"""
Trong tổng số **{format_integer(summary['total_candidates'])} thí sinh**, có
**{format_integer(summary['valid_literature'])} thí sinh có điểm Ngữ văn** và
**{format_integer(summary['missing_literature'])} thí sinh thiếu điểm Ngữ văn**.

Các chỉ số chính gồm:

- điểm trung bình: khoảng **{format_decimal(summary['mean_literature'], 4)}**;
- Q1: **{format_decimal(literature_quartiles['q1'], 2)}**;
- trung vị: **{format_decimal(literature_quartiles['median_value'], 2)}**;
- Q3: **{format_decimal(literature_quartiles['q3'], 2)}**;
- IQR: **{format_decimal(literature_quartiles['iqr'], 2)}**;
- ngưỡng dưới IQR: **{format_decimal(literature_quartiles['lower_bound'], 3)}**;
- ngưỡng trên IQR: **{format_decimal(literature_quartiles['upper_bound'], 3)}**.

Khoảng 50% thí sinh có điểm Ngữ văn từ
**{format_decimal(literature_quartiles['q1'], 2)}** đến
**{format_decimal(literature_quartiles['q3'], 2)}**. Trung vị cao hơn trung bình
khoảng **{format_decimal(literature_median_minus_mean, 4)} điểm**.

Có **{format_integer(literature_outlier_count)} bài thi** được xác định là ngoại lai
theo quy tắc IQR, chiếm khoảng **{format_percent(literature_outlier_rate, 2)}** số
bài Ngữ văn hợp lệ. Một điểm nằm ngoài giới hạn IQR vẫn có thể là kết quả thi hợp lệ,
vì vậy không nên tự động xóa nếu chưa kiểm tra quy tắc nghiệp vụ.
"""
)


# =========================================================
# 13. PHẦN 5 - XÁC SUẤT THỰC NGHIỆM
# =========================================================
st.header("5. Phân tích xác suất thực nghiệm")

math_below_rate = safe_percent(
    summary["math_below_5"],
    summary["valid_math"],
)
literature_below_rate = safe_percent(
    summary["literature_below_5"],
    summary["valid_literature"],
)

st.subheader("5.1. Xác suất điểm Toán dưới 5")
st.markdown(
    f"""
Các bản ghi `Toan IS NULL` không nằm trong mẫu số vì chưa có điểm quan sát thực tế.

\\[
P(\\text{{Toán}}<5)
=\\frac{{{format_integer(summary['math_below_5'])}}}
{{{format_integer(summary['valid_math'])}}}\\times100
\\approx {format_decimal(math_below_rate, 3)}\\%
\\]

Có **{format_integer(summary['math_below_5'])} thí sinh dưới 5 điểm Toán** trên tổng số
**{format_integer(summary['valid_math'])} thí sinh có điểm Toán**. Nếu chọn ngẫu nhiên
một thí sinh trong nhóm đã có điểm Toán, xác suất thí sinh đó dưới 5 điểm là khoảng
**{format_percent(math_below_rate, 3)}**.
"""
)

st.subheader("5.2. Xác suất điểm Ngữ văn dưới 5")
st.markdown(
    f"""
Các bản ghi `NguVan IS NULL` không nằm trong mẫu số vì chưa có điểm quan sát thực tế.

\\[
P(\\text{{Ngữ văn}}<5)
=\\frac{{{format_integer(summary['literature_below_5'])}}}
{{{format_integer(summary['valid_literature'])}}}\\times100
\\approx {format_decimal(literature_below_rate, 3)}\\%
\\]

Có **{format_integer(summary['literature_below_5'])} thí sinh dưới 5 điểm Ngữ văn**
trên tổng số **{format_integer(summary['valid_literature'])} thí sinh có điểm Ngữ văn**.
Xác suất thực nghiệm tương ứng là khoảng **{format_percent(literature_below_rate, 3)}**.
"""
)

st.subheader("So sánh xác suất điểm dưới 5 giữa hai môn")
probability_difference = math_below_rate - literature_below_rate
probability_ratio = safe_ratio(math_below_rate, literature_below_rate)

probability_df = pd.DataFrame(
    {
        "Môn": ["Toán", "Ngữ văn"],
        "Tỷ lệ dưới 5": [math_below_rate, literature_below_rate],
        "Số thí sinh": [
            summary["math_below_5"],
            summary["literature_below_5"],
        ],
    }
)

probability_figure = px.bar(
    probability_df,
    x="Môn",
    y="Tỷ lệ dưới 5",
    text=probability_df["Tỷ lệ dưới 5"].map(lambda x: f"{x:.3f}%"),
    custom_data=["Số thí sinh"],
    title="So sánh tỷ lệ điểm dưới 5",
)
probability_figure.update_traces(
    hovertemplate=(
        "Môn: %{x}<br>"
        "Tỷ lệ dưới 5: %{y:.3f}%<br>"
        "Số thí sinh: %{customdata[0]:,}<extra></extra>"
    )
)
probability_figure.update_layout(yaxis_title="Tỷ lệ thí sinh (%)")
st.plotly_chart(probability_figure, use_container_width=True)

st.markdown(
    f"""
- Toán dưới 5: **{format_percent(math_below_rate, 3)}**.
- Ngữ văn dưới 5: **{format_percent(literature_below_rate, 3)}**.

Chênh lệch giữa hai tỷ lệ là **{format_decimal(probability_difference, 3)} điểm phần trăm**.
Tỷ lệ dưới 5 môn Toán cao gấp khoảng **{format_decimal(probability_ratio, 2)} lần**
môn Ngữ văn trong phạm vi dữ liệu đang chọn.

Tuy nhiên, chưa thể chỉ dựa vào tỷ lệ này để kết luận đề Toán khó hơn đề Ngữ văn.
Sự khác biệt có thể liên quan đến cấu trúc đề, phương pháp chấm, mức độ phân hóa
và đặc điểm năng lực của thí sinh.
"""
)

st.subheader("5.3. Phân nhóm đồng thời điểm Toán và Ngữ văn")
st.write(
    "Các thí sinh có đủ cả hai điểm được chia thành 9 nhóm không trùng lặp "
    "theo ba mức `< 5`, `= 5` và `> 5`."
)

score_groups = get_score_groups(selected_province)
score_groups_plot = score_groups.sort_values("percentage", ascending=True)

score_group_figure = px.bar(
    score_groups_plot,
    x="percentage",
    y="score_group",
    orientation="h",
    custom_data=["candidate_count"],
    text=score_groups_plot.apply(
        lambda row: (
            f"{row['percentage']:.2f}% | "
            f"{int(row['candidate_count']):,} thí sinh"
        ),
        axis=1,
    ),
    labels={
        "percentage": "Tỷ lệ thí sinh (%)",
        "score_group": "Nhóm điểm",
    },
    title="Phân nhóm điểm Toán và Ngữ văn",
)
score_group_figure.update_traces(
    textposition="outside",
    cliponaxis=False,
    hovertemplate=(
        "%{y}<br>"
        "Tỷ lệ: %{x:.2f}%<br>"
        "Số thí sinh: %{customdata[0]:,}<extra></extra>"
    ),
)
score_group_figure.update_layout(
    height=620,
    margin={"l": 20, "r": 160, "t": 70, "b": 40},
)
st.plotly_chart(score_group_figure, use_container_width=True)

st.dataframe(
    score_groups.rename(
        columns={
            "score_group": "Nhóm điểm",
            "candidate_count": "Số thí sinh",
            "percentage": "Tỷ lệ (%)",
        }
    ),
    use_container_width=True,
    hide_index=True,
    column_config={
        "Số thí sinh": st.column_config.NumberColumn(format="%d"),
        "Tỷ lệ (%)": st.column_config.NumberColumn(format="%.2f%%"),
    },
)

score_lookup = score_groups.set_index("score_group")
complete_both = int(score_groups["candidate_count"].sum())

both_above = score_lookup.loc[
    "Toán > 5 và Ngữ văn > 5", "candidate_count"
]
both_below = score_lookup.loc[
    "Toán < 5 và Ngữ văn < 5", "candidate_count"
]
math_above_lit_below = score_lookup.loc[
    "Toán > 5 và Ngữ văn < 5", "candidate_count"
]
math_below_lit_above = score_lookup.loc[
    "Toán < 5 và Ngữ văn > 5", "candidate_count"
]

equal_five_mask = score_groups["score_group"].str.contains("= 5", regex=False)
equal_five_total = int(score_groups.loc[equal_five_mask, "candidate_count"].sum())
equal_five_rate = safe_percent(equal_five_total, complete_both)

reverse_group_ratio = safe_ratio(
    math_below_lit_above,
    math_above_lit_below,
)

st.subheader("Nhận xét phân nhóm điểm Toán và Ngữ văn")
st.markdown(
    f"""
Trong tổng số **{format_integer(complete_both)} thí sinh có đầy đủ cả hai điểm**:

- **{format_integer(both_above)} thí sinh** có cả Toán và Ngữ văn trên 5,
  chiếm **{format_percent(safe_percent(both_above, complete_both), 2)}**;
- **{format_integer(math_below_lit_above)} thí sinh** có Toán dưới 5 nhưng
  Ngữ văn trên 5, chiếm **{format_percent(safe_percent(math_below_lit_above, complete_both), 2)}**;
- **{format_integer(both_below)} thí sinh** dưới 5 ở cả hai môn,
  chiếm **{format_percent(safe_percent(both_below, complete_both), 2)}**;
- **{format_integer(math_above_lit_below)} thí sinh** có Toán trên 5 nhưng
  Ngữ văn dưới 5, chiếm **{format_percent(safe_percent(math_above_lit_below, complete_both), 2)}**.

Nhóm Toán dưới 5 nhưng Ngữ văn trên 5 lớn hơn nhóm ngược lại khoảng
**{format_decimal(reverse_group_ratio, 2)} lần**.

Tổng các nhóm có ít nhất một môn bằng đúng 5 là
**{format_integer(equal_five_total)} thí sinh**, tương đương khoảng
**{format_percent(equal_five_rate, 2)}**.

Tổng số lượng của 9 nhóm bằng đúng **{format_integer(complete_both)} thí sinh** có đủ
hai điểm. Như vậy, các nhóm không bị trùng lặp và không bỏ sót dữ liệu.
Tổng tỷ lệ hiển thị có thể chênh 0,01% do làm tròn từng nhóm.
"""
)


# =========================================================
# 14. PHẦN 6 - PEARSON
# =========================================================
st.header("6. Mối liên hệ giữa điểm Toán và Ngữ văn")
st.write(
    "Hệ số Pearson đo chiều và mức độ liên hệ tuyến tính giữa hai môn. "
    "Chỉ các thí sinh có đầy đủ cả hai điểm được đưa vào phép tính."
)

pearson_value = get_pearson(selected_province)
scatter_df = get_scatter_sample(selected_province, scatter_sample_size)

if pearson_value is None or scatter_df.empty:
    st.info("Không đủ dữ liệu để tính hoặc trực quan hóa tương quan Pearson.")
else:
    scatter_figure = px.scatter(
        scatter_df,
        x="Toan",
        y="NguVan",
        opacity=0.25,
        labels={"Toan": "Điểm Toán", "NguVan": "Điểm Ngữ văn"},
        title=(
            "Mối liên hệ giữa điểm Toán và Ngữ văn "
            f"— Pearson r = {pearson_value:.4f}"
        ),
    )
    scatter_figure.update_traces(
        marker={"size": 6},
        selector={"mode": "markers"},
    )

    x_mean = scatter_df["Toan"].mean()
    y_mean = scatter_df["NguVan"].mean()
    x_deviation = scatter_df["Toan"] - x_mean
    y_deviation = scatter_df["NguVan"] - y_mean
    x_variance_sum = (x_deviation ** 2).sum()

    if x_variance_sum > 0:
        slope = (x_deviation * y_deviation).sum() / x_variance_sum
        intercept = y_mean - slope * x_mean
        line_x = [scatter_df["Toan"].min(), scatter_df["Toan"].max()]
        line_y = [slope * value + intercept for value in line_x]

        scatter_figure.add_trace(
            go.Scatter(
                x=line_x,
                y=line_y,
                mode="lines",
                name="Đường xu hướng",
                hovertemplate=(
                    "Điểm Toán: %{x:.2f}<br>"
                    "Giá trị xu hướng Văn: %{y:.2f}<extra></extra>"
                ),
            )
        )
    scatter_figure.update_layout(
        xaxis={"range": [0, 10]},
        yaxis={"range": [0, 10]},
        height=570,
    )
    st.plotly_chart(scatter_figure, use_container_width=True)

    if pearson_value > 0:
        relation_direction = "tương quan thuận"
    elif pearson_value < 0:
        relation_direction = "tương quan nghịch"
    else:
        relation_direction = "gần như không có tương quan tuyến tính"

    pearson_strength = abs(pearson_value)
    if pearson_strength < 0.2:
        relation_level = "rất yếu"
    elif pearson_strength < 0.4:
        relation_level = "yếu"
    elif pearson_strength < 0.6:
        relation_level = "trung bình"
    elif pearson_strength < 0.8:
        relation_level = "khá mạnh"
    else:
        relation_level = "mạnh"

    st.subheader("Kết luận về tương quan Pearson")
    st.markdown(
        f"""
Hệ số tương quan Pearson giữa điểm Toán và Ngữ văn là
**r = {format_decimal(pearson_value, 4)}**. Giá trị này cho thấy hai môn có
**{relation_direction} ở mức {relation_level}**.

Điểm Toán và Ngữ văn có xu hướng biến động cùng chiều hoặc ngược chiều theo dấu
của hệ số, nhưng biểu đồ vẫn có thể phân tán rộng quanh đường xu hướng. Vì vậy,
không nên dùng riêng điểm môn này để dự đoán chính xác môn kia.

Hệ số Pearson được tính trên toàn bộ **{format_integer(summary['complete_both'])} thí sinh
có đủ hai điểm** trong phạm vi lọc. Biểu đồ chỉ hiển thị tối đa
**{format_integer(len(scatter_df))} thí sinh** để giảm thời gian vẽ.

Tương quan không chứng minh quan hệ nguyên nhân – kết quả.
"""
    )


# =========================================================
# 15. PHẦN 7 - THỬ NGHIỆM TONGDIEM
# =========================================================
st.header("7. Phân tích thử nghiệm nhóm thí sinh thiếu điểm Toán")
st.markdown(
    """
Phần này giữ nguyên phép tính `mon_conlai = TongDiem / 2` để kiểm tra một giả định
thử nghiệm. Kết quả chỉ mang tính thăm dò, không phải xếp loại học lực chính thức,
vì `TongDiem` có thể được tạo từ số lượng môn khác nhau.
"""
)

st.subheader("7.1. Biểu đồ phân bố nhóm xếp loại thử nghiệm")
experimental_df = get_experimental_ranking(selected_province)

experimental_figure = px.bar(
    experimental_df,
    x="ranking",
    y="candidate_count",
    text=experimental_df.apply(
        lambda row: (
            f"{int(row['candidate_count']):,} | "
            f"{row['percentage']:.2f}%"
        ),
        axis=1,
    ),
    custom_data=["percentage", "max_experimental_score"],
    category_orders={
        "ranking": ["Yếu", "Trung bình", "Khá", "Giỏi"],
    },
    labels={
        "ranking": "Nhóm thử nghiệm",
        "candidate_count": "Số thí sinh",
    },
    title="Phân bố nhóm xếp loại thử nghiệm từ TongDiem / 2",
)
experimental_figure.update_traces(
    textposition="outside",
    hovertemplate=(
        "Nhóm: %{x}<br>"
        "Số thí sinh: %{y:,}<br>"
        "Tỷ lệ: %{customdata[0]:.2f}%<br>"
        "Giá trị thử nghiệm lớn nhất: %{customdata[1]:.3f}<extra></extra>"
    ),
)
st.plotly_chart(experimental_figure, use_container_width=True)

st.subheader("7.2. Kết luận thận trọng")
experimental_total = int(experimental_df["candidate_count"].sum())
max_experimental_score = experimental_df["max_experimental_score"].max()

experimental_lines = []
for _, row in experimental_df.iterrows():
    experimental_lines.append(
        f"- {row['ranking']}: **{format_integer(row['candidate_count'])} thí sinh**, "
        f"chiếm **{format_percent(row['percentage'], 2)}**;"
    )

st.markdown(
    "\n".join(experimental_lines)
    + f"""

Tổng số bản ghi được đưa vào thử nghiệm là **{format_integer(experimental_total)}**.
Giá trị lớn nhất của `TongDiem / 2` là **{format_decimal(max_experimental_score, 3)}**.
Nếu giá trị này lớn hơn 10, giả định `TongDiem` luôn là tổng của đúng hai môn
không phù hợp với toàn bộ dữ liệu.

Vì vậy, không nên dùng các tỷ lệ thử nghiệm trên để kết luận học lực, dự đoán đỗ
hoặc trượt, thay thế điểm Toán bị thiếu hay đưa ra quyết định hỗ trợ thí sinh.
Muốn phân tích chính xác cần xác định rõ các môn cấu thành `TongDiem` và số môn
thực tế của từng bản ghi.
"""
)


# =========================================================
# 16. PHẦN 8 - HẠN CHẾ
# =========================================================
st.header("8. Hạn chế của phân tích")
st.markdown(
    """
1. **Chưa biết nguyên nhân của dữ liệu thiếu:** `NULL` không cho biết thí sinh
   vắng thi, miễn thi, bị hủy kết quả hay dữ liệu bị thiếu khi thu thập.
2. **So sánh tỉnh cần chuẩn hóa:** nên so sánh tỷ lệ trên tổng số thí sinh của
   từng tỉnh thay vì chỉ nhìn số lượng tuyệt đối.
3. **Mã tỉnh chưa được chuyển thành tên tỉnh:** làm giảm khả năng đọc và diễn giải.
4. **Ngoại lai IQR không đồng nghĩa dữ liệu sai:** điểm thấp vẫn có thể là kết quả
   thi hợp lệ.
5. **Pearson chỉ đo quan hệ tuyến tính:** không phản ánh đầy đủ quan hệ phi tuyến
   và không chứng minh nhân quả.
6. **Dữ liệu mới thuộc một năm:** chưa thể xác định xu hướng tăng, giảm nếu chưa
   so sánh nhiều năm.
7. **Thiếu các biến giải thích:** chưa có đủ thông tin về trường học, khu vực,
   điều kiện học tập hoặc đặc điểm cá nhân.
8. **Chưa có biến mục tiêu chính thức cho Machine Learning:** cần xác định rõ
   bài toán trước khi huấn luyện mô hình.
"""
)


# =========================================================
# 17. PHẦN 9 - KẾT LUẬN TỔNG HỢP
# =========================================================
st.header("9. Kết luận tổng hợp")

st.subheader("Chất lượng dữ liệu")
st.write(
    f"Tập dữ liệu trong phạm vi {report_scope} có "
    f"**{format_integer(summary['total_candidates'])} thí sinh**. Trong đó, "
    f"**{format_integer(summary['missing_math'])} thí sinh thiếu điểm Toán**, "
    f"**{format_integer(summary['missing_literature'])} thí sinh thiếu điểm Ngữ văn**, "
    f"**{format_integer(summary['missing_both'])} thí sinh thiếu đồng thời hai môn** "
    f"và **{format_integer(summary['complete_both'])} thí sinh có đủ cả hai điểm**."
)

st.subheader("Môn Toán")
st.write(
    f"Điểm Toán có trung bình khoảng **{format_decimal(summary['mean_math'], 4)}**, "
    f"trung vị **{format_decimal(math_quartiles['median_value'], 2)}** và 50% điểm nằm từ "
    f"**{format_decimal(math_quartiles['q1'], 2)} đến {format_decimal(math_quartiles['q3'], 2)}**. "
    f"Quy tắc IQR phát hiện **{format_integer(math_outlier_count)} ngoại lai**."
)

st.subheader("Môn Ngữ văn")
st.write(
    f"Điểm Ngữ văn có trung bình khoảng **{format_decimal(summary['mean_literature'], 4)}**, "
    f"trung vị **{format_decimal(literature_quartiles['median_value'], 2)}** và 50% điểm nằm từ "
    f"**{format_decimal(literature_quartiles['q1'], 2)} đến {format_decimal(literature_quartiles['q3'], 2)}**. "
    f"Có **{format_integer(literature_outlier_count)} bài**, tương đương khoảng "
    f"**{format_percent(literature_outlier_rate, 2)}**, được xác định là ngoại lai theo IQR."
)

st.subheader("Xác suất điểm dưới 5")
st.markdown(
    f"""
- Toán dưới 5: **{format_percent(math_below_rate, 3)}**.
- Ngữ văn dưới 5: **{format_percent(literature_below_rate, 3)}**.

Tỷ lệ dưới 5 môn Toán cao hơn khoảng
**{format_decimal(probability_difference, 3)} điểm phần trăm**, tương đương gấp
khoảng **{format_decimal(probability_ratio, 2)} lần** môn Ngữ văn.
"""
)

st.subheader("Phân nhóm đồng thời hai môn")
st.markdown(
    f"""
- **{format_percent(safe_percent(both_above, complete_both), 2)}** có cả hai môn trên 5;
- **{format_percent(safe_percent(math_below_lit_above, complete_both), 2)}** có Toán dưới 5 nhưng Ngữ văn trên 5;
- **{format_percent(safe_percent(both_below, complete_both), 2)}** có cả hai môn dưới 5;
- **{format_percent(safe_percent(math_above_lit_below, complete_both), 2)}** có Toán trên 5 nhưng Ngữ văn dưới 5;
- khoảng **{format_percent(equal_five_rate, 2)}** có ít nhất một môn bằng đúng 5.

Chín nhóm bao phủ đúng toàn bộ **{format_integer(complete_both)} thí sinh có đủ hai điểm**,
không bị trùng hoặc bỏ sót.
"""
)

st.subheader("Mối liên hệ hai môn")
if pearson_value is not None:
    st.write(
        f"Pearson bằng **{format_decimal(pearson_value, 4)}**, cho thấy mối liên hệ "
        "tuyến tính giữa hai môn. Hai điểm có xu hướng biến động theo cùng chiều "
        "khi hệ số dương, nhưng chưa đủ để dự đoán chính xác môn này chỉ từ môn kia."
    )
else:
    st.write("Không đủ dữ liệu để tính hệ số Pearson trong phạm vi đã chọn.")

st.subheader("Kết luận cuối")
st.markdown(
    f"""
Dashboard đã tái hiện các bước chính của Notebook: kiểm tra dữ liệu thiếu, thống kê
mô tả, kiểm tra ngoại lai, so sánh xử lý `NULL`, tính xác suất, phân nhóm và phân
tích tương quan.

Trong phạm vi **{report_scope}**, điểm dưới 5 xuất hiện
**{'phổ biến hơn ở môn Toán' if math_below_rate > literature_below_rate else 'phổ biến hơn ở môn Ngữ văn'}**.
Có **{format_percent(safe_percent(both_above, complete_both), 2)}** thí sinh có điểm
trên 5 ở cả hai môn. Các kết quả này là cơ sở cho phân tích sâu hơn nhưng chưa đủ
để giải thích nguyên nhân hoặc kết luận chính thức về khả năng đỗ, trượt của từng
thí sinh.
"""
)


# =========================================================
# 18. PHẦN 10 - HƯỚNG PHÁT TRIỂN
# =========================================================
st.header("10. Hướng phát triển phân tích")
st.markdown(
    """
### 1. Xác suất có điều kiện

Có thể tính:

\\[
P(\\text{Ngữ văn}<5\\mid\\text{Toán}<5)
\\]

và:

\\[
P(\\text{Toán}<5\\mid\\text{Ngữ văn}<5)
\\]

Hai xác suất này giúp đánh giá mức độ đồng thời xuất hiện điểm thấp giữa hai môn.

### 2. Phân tích theo tỉnh

Nên bổ sung tổng số thí sinh, điểm trung bình, trung vị, tỷ lệ thiếu điểm, tỷ lệ
dưới 5 và độ lệch chuẩn của từng tỉnh. Khi so sánh, nên ưu tiên tỷ lệ thay vì số
lượng tuyệt đối.

### 3. So sánh nhiều năm

Nếu có dữ liệu 2018–2026, có thể đánh giá xu hướng điểm trung bình, tỷ lệ dưới 5,
mức phân tán và sự thay đổi tương quan giữa các môn.

### 4. Hồi quy tuyến tính

Có thể thử mô hình hồi quy giữa Toán và Ngữ văn, sau đó đánh giá bằng R², MAE,
MSE, RMSE và biểu đồ phần dư. Mô hình chỉ mang ý nghĩa phân tích, không thay thế
điểm thi thật.

### 5. Machine Learning

Chỉ nên áp dụng ML sau khi xác định rõ biến mục tiêu, ví dụ:

- phân loại nguy cơ dưới 5 điểm;
- dự đoán điểm một môn;
- phân nhóm thí sinh theo nhiều môn;
- phát hiện bản ghi bất thường.

Mô hình cần được đánh giá trên tập kiểm tra riêng và không nên chỉ sử dụng
Accuracy khi dữ liệu mất cân bằng.
"""
)


# =========================================================
# 19. BẢNG DỮ LIỆU CÓ THỂ SẮP XẾP
# =========================================================
st.header("Bảng dữ liệu chi tiết")
st.write(
    "Bảng dưới đây lấy một số dòng theo giới hạn ở Sidebar. Bạn có thể nhấn vào "
    "tên cột để sắp xếp, kéo rộng cột hoặc tìm kiếm trực tiếp trong bảng."
)

try:
    data_table = get_data_table(selected_province, table_row_limit)
except SQLAlchemyError as error:
    st.warning(f"Không thể tải bảng dữ liệu chi tiết: {error}")
else:
    st.dataframe(
        data_table,
        use_container_width=True,
        hide_index=True,
        height=620,
        column_config={
            "SBD": st.column_config.TextColumn("Số báo danh"),
            "Nam": st.column_config.NumberColumn("Năm", format="%d"),
            "Tinh": st.column_config.TextColumn("Mã tỉnh"),
            "Toan": st.column_config.NumberColumn("Toán", format="%.2f"),
            "NguVan": st.column_config.NumberColumn("Ngữ văn", format="%.2f"),
            "VatLy": st.column_config.NumberColumn("Vật lý", format="%.2f"),
            "HoaHoc": st.column_config.NumberColumn("Hóa học", format="%.2f"),
            "SinhHoc": st.column_config.NumberColumn("Sinh học", format="%.2f"),
            "LichSu": st.column_config.NumberColumn("Lịch sử", format="%.2f"),
            "DiaLy": st.column_config.NumberColumn("Địa lý", format="%.2f"),
            "KinhTePhapLuat": st.column_config.NumberColumn(
                "Kinh tế pháp luật",
                format="%.2f",
            ),
            "TinHoc": st.column_config.NumberColumn("Tin học", format="%.2f"),
            "NgoaiNgu": st.column_config.NumberColumn("Ngoại ngữ", format="%.2f"),
            "TongDiem": st.column_config.NumberColumn("Tổng điểm", format="%.2f"),
        },
    )
