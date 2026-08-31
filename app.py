import sys
import os
import tempfile
import numpy as np
import pandas as pd

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QGroupBox, QLabel, QComboBox, QPushButton,
    QTabWidget, QTableWidget, QTableWidgetItem, QFileDialog, QMessageBox,
    QHeaderView, QListWidget, QListWidgetItem
)

import matplotlib
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors


# -----------------------------------------------------------------------------
# SYNTHETIC DATA GENERATOR & METRICS ENGINE
# -----------------------------------------------------------------------------

class LoadEngine:
    """Generates synthetic 12-week workload data for 20 athletes and calculates ACWR, Monotony, and Strain."""
    
    ATHLETE_NAMES = [f"Athlete {i+1:02d}" for i in range(20)]

    @classmethod
    def generate_team_data(cls, num_weeks=12):
        num_days = num_weeks * 7
        date_range = pd.date_range(end=pd.Timestamp.today(), periods=num_days, freq='D')
        
        all_records = []
        np.random.seed(42)  # Consistent baseline generation

        for athlete in cls.ATHLETE_NAMES:
            # Base physiological profiles
            base_rpe = np.random.uniform(4.0, 7.0)
            base_duration = np.random.uniform(60, 90)
            
            for day_idx, date in enumerate(date_range):
                # Simulate training cycles (hard days, rest days, load spikes around week 8-9)
                is_rest = (day_idx % 7 == 6)  # Weekly rest day
                is_spike = (50 <= day_idx <= 60 and np.random.rand() > 0.4)  # Overload phase

                if is_rest:
                    duration = 0
                    rpe = 0
                    distance = 0.0
                    hi_distance = 0.0
                    strength_load = 0.0
                    recovery = np.random.uniform(75, 95)
                else:
                    mult = 1.4 if is_spike else 1.0
                    duration = max(20, int(np.random.normal(base_duration * mult, 15)))
                    rpe = np.clip(np.random.normal(base_rpe * mult, 1.2), 1, 10)
                    distance = round(max(1.0, np.random.normal(6.0 * mult, 1.8)), 2)
                    hi_distance = round(max(0.0, distance * np.random.uniform(0.1, 0.35)), 2)
                    strength_load = round(max(0.0, np.random.normal(1200 * mult, 300)), 1)
                    recovery = np.clip(np.random.normal(70 - (rpe * 3), 10), 20, 98)

                session_load = duration * rpe
                
                all_records.append({
                    "Date": date,
                    "Athlete": athlete,
                    "Duration_min": duration,
                    "sRPE": round(rpe, 1),
                    "Distance_km": distance,
                    "High_Intensity_Dist_km": hi_distance,
                    "Strength_Load_kg": strength_load,
                    "Recovery_Score": round(recovery, 1),
                    "Session_Load": session_load,
                    "Daily_Load": session_load  # Single session daily model
                })

        df = pd.DataFrame(all_records)
        df = cls.calculate_rolling_metrics(df)
        return df

    @staticmethod
    def calculate_rolling_metrics(df):
        """Calculates Rolling 7d/28d loads, ACWR, Monotony, and Strain per athlete."""
        processed_dfs = []

        for athlete, group in df.groupby("Athlete"):
            group = group.sort_values("Date").copy()
            
            # Weekly Load (Sum of 7-day calendar windows)
            group["Weekly_Load"] = group["Daily_Load"].rolling(window=7, min_periods=1).sum()

            # Rolling 7-Day (Acute) & Rolling 28-Day (Chronic) Loads
            group["Rolling_7d_Acute"] = group["Daily_Load"].rolling(window=7, min_periods=1).mean() * 7
            group["Rolling_28d_Chronic"] = group["Daily_Load"].rolling(window=28, min_periods=1).mean() * 7

            # Acute:Chronic Workload Ratio (ACWR)
            group["ACWR"] = group["Rolling_7d_Acute"] / group["Rolling_28d_Chronic"].replace(0, np.nan)
            group["ACWR"] = group["ACWR"].fillna(0.0).round(2)

            # Monotony = 7-day Mean Daily Load / 7-day Std Dev
            rolling_mean = group["Daily_Load"].rolling(window=7, min_periods=1).mean()
            rolling_std = group["Daily_Load"].rolling(window=7, min_periods=1).std().fillna(1.0)
            rolling_std = rolling_std.replace(0, 1.0)  # Avoid division by zero
            group["Monotony"] = (rolling_mean / rolling_std).round(2)

            # Strain = Weekly Load * Monotony
            group["Strain"] = (group["Weekly_Load"] * group["Monotony"]).round(1)

            processed_dfs.append(group)

        return pd.concat(processed_dfs, ignore_index=True)


# -----------------------------------------------------------------------------
# MAIN APPLICATION WINDOW
# -----------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Athlete Load Management Engine (Team & Individual Analytics)")
        self.resize(1420, 900)

        # Load Dataset
        self.raw_df = LoadEngine.generate_team_data(num_weeks=12)
        self.current_athlete = "All Athletes (Team Average)"

        self.init_ui()
        self.update_dashboard()

    def init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)

        # ---------------------------------------------------------------------
        # LEFT PANEL: Controls, Filters & Flags
        # ---------------------------------------------------------------------
        left_panel = QWidget()
        left_panel.setFixedWidth(320)
        left_layout = QVBoxLayout(left_panel)

        # Athlete Selector Group
        filter_group = QGroupBox("Athlete Selection")
        filter_layout = QVBoxLayout(filter_group)
        
        self.combo_athlete = QComboBox()
        self.combo_athlete.addItem("All Athletes (Team Average)")
        self.combo_athlete.addItems(LoadEngine.ATHLETE_NAMES)
        self.combo_athlete.currentTextChanged.connect(self.on_athlete_changed)
        filter_layout.addWidget(self.combo_athlete)
        left_layout.addWidget(filter_group)

        # Automatic Risk Flags Box
        flags_group = QGroupBox("Automated Risk Flags")
        flags_layout = QVBoxLayout(flags_group)

        self.list_flags = QListWidget()
        self.list_flags.setStyleSheet("background-color: #1e1e1e; color: #ff5555; font-family: monospace; font-size: 11px;")
        flags_layout.addWidget(self.list_flags)
        left_layout.addWidget(flags_group)

        # Export Controls Box
        export_group = QGroupBox("Data & Diagnostics Export")
        export_layout = QVBoxLayout(export_group)

        self.btn_export_csv = QPushButton("Export Filtered CSV")
        self.btn_export_csv.setStyleSheet("font-weight: bold; padding: 6px;")
        self.btn_export_csv.clicked.connect(self.export_csv)
        export_layout.addWidget(self.btn_export_csv)

        self.btn_export_pdf = QPushButton("Export Diagnostic PDF Report")
        self.btn_export_pdf.setStyleSheet("font-weight: bold; background-color: #28a745; color: white; padding: 7px;")
        self.btn_export_pdf.clicked.connect(self.export_pdf)
        export_layout.addWidget(self.btn_export_pdf)

        left_layout.addWidget(export_group)
        left_layout.addStretch()

        main_layout.addWidget(left_panel)

        # ---------------------------------------------------------------------
        # RIGHT PANEL: Visualizations & Tables
        # ---------------------------------------------------------------------
        self.tabs = QTabWidget()

        # Tab 1: Daily & Weekly Workload Charts
        self.tab_loads = QWidget()
        t1_layout = QVBoxLayout(self.tab_loads)
        self.fig_loads = Figure(figsize=(8, 6))
        self.canvas_loads = FigureCanvas(self.fig_loads)
        t1_layout.addWidget(self.canvas_loads)
        self.tabs.addTab(self.tab_loads, "Daily & Weekly Load Charts")

        # Tab 2: Acute/Chronic & Recovery Trends
        self.tab_trends = QWidget()
        t2_layout = QVBoxLayout(self.tab_trends)
        self.fig_trends = Figure(figsize=(8, 6))
        self.canvas_trends = FigureCanvas(self.fig_trends)
        t2_layout.addWidget(self.canvas_trends)
        self.tabs.addTab(self.tab_trends, "ACWR & Recovery Trends")

        # Tab 3: Detailed Data Table
        self.tab_data = QWidget()
        t3_layout = QVBoxLayout(self.tab_data)
        self.data_table = QTableWidget()
        t3_layout.addWidget(self.data_table)
        self.tabs.addTab(self.tab_data, "Metrics Log Table")

        main_layout.addWidget(self.tabs)

    # -------------------------------------------------------------------------
    # FILTERING & DATA PREPARATION
    # -------------------------------------------------------------------------

    def on_athlete_changed(self, text):
        self.current_athlete = text
        self.update_dashboard()

    def get_filtered_df(self):
        if self.current_athlete == "All Athletes (Team Average)":
            # Group by Date to get Team Mean
            df = self.raw_df.groupby("Date", as_index=False).mean(numeric_only=True)
            df["Athlete"] = "Team Average"
            return df
        else:
            return self.raw_df[self.raw_df["Athlete"] == self.current_athlete].copy()

    def update_dashboard(self):
        df = self.get_filtered_df()
        self.update_flags(df)
        self.plot_loads(df)
        self.plot_trends(df)
        self.update_table(df)

    # -------------------------------------------------------------------------
    # AUTOMATED RISK FLAGS
    # -------------------------------------------------------------------------

    def update_flags(self, df):
        self.list_flags.clear()
        
        # Check recent 14-day window for active flags
        recent_df = df.tail(14)
        
        flags = []
        for _, row in recent_df.iterrows():
            date_str = row["Date"].strftime("%Y-%m-%d")
            
            if row["ACWR"] > 1.5:
                flags.append(f"[{date_str}] ACWR SPIKE: {row['ACWR']:.2f} (>1.5)")
            if row["Monotony"] > 2.0:
                flags.append(f"[{date_str}] HIGH MONOTONY: {row['Monotony']:.2f} (>2.0)")
            if row["Strain"] > 6000:
                flags.append(f"[{date_str}] EXCESSIVE STRAIN: {row['Strain']:.0f} AU")
            if row["Recovery_Score"] < 50:
                flags.append(f"[{date_str}] RECOVERY DEFICIT: {row['Recovery_Score']:.1f}")

        if not flags:
            item = QListWidgetItem("No acute risk flags detected.")
            item.setForeground(Qt.green)
            self.list_flags.addItem(item)
        else:
            for flag in flags:
                self.list_flags.addItem(flag)

    # -------------------------------------------------------------------------
    # MATPLOTLIB CHARTS
    # -------------------------------------------------------------------------

    def plot_loads(self, df):
        self.fig_loads.clear()
        
        # Subplot 1: Daily Load
        ax1 = self.fig_loads.add_subplot(211)
        ax1.bar(df["Date"], df["Daily_Load"], color="#0066cc", alpha=0.7, label="Daily Session Load (AU)")
        
        # Highlight Load Spikes (> 1.5 Std Above Mean)
        mean_load = df["Daily_Load"].mean()
        std_load = df["Daily_Load"].std()
        spikes = df[df["Daily_Load"] > (mean_load + 1.5 * std_load)]
        ax1.scatter(spikes["Date"], spikes["Daily_Load"], color="red", zorder=5, label="Load Spike")

        ax1.set_ylabel("Load (AU)")
        ax1.set_title(f"Daily Session Workload - {self.current_athlete}")
        ax1.grid(True, linestyle=":", alpha=0.6)
        ax1.legend(loc="upper left")

        # Subplot 2: Weekly Load & Strain
        ax2 = self.fig_loads.add_subplot(212)
        ax2.plot(df["Date"], df["Weekly_Load"], color="#28a745", linewidth=2, label="Weekly Rolling Load")
        ax2.plot(df["Date"], df["Strain"], color="#dc3545", linestyle="--", label="Training Strain")
        
        ax2.set_ylabel("Accumulated Load / Strain")
        ax2.set_xlabel("Date")
        ax2.set_title("Accumulated 7-Day Load & Training Strain")
        ax2.grid(True, linestyle=":", alpha=0.6)
        ax2.legend(loc="upper left")

        self.fig_loads.tight_layout()
        self.canvas_loads.draw()

    def plot_trends(self, df):
        self.fig_trends.clear()

        # Subplot 1: ACWR Ratio
        ax1 = self.fig_trends.add_subplot(211)
        ax1.plot(df["Date"], df["ACWR"], color="#6f42c1", linewidth=2, label="Acute:Chronic Ratio (ACWR)")
        ax1.axhspan(0.8, 1.3, color="green", alpha=0.15, label="Optimal Zone (0.8 - 1.3)")
        ax1.axhline(1.5, color="red", linestyle="--", label="High Risk Threshold (1.5)")

        ax1.set_ylabel("ACWR Ratio")
        ax1.set_title(f"Acute:Chronic Workload Ratio (ACWR) - {self.current_athlete}")
        ax1.grid(True, linestyle=":", alpha=0.6)
        ax1.legend(loc="upper left")

        # Subplot 2: Recovery Score
        ax2 = self.fig_trends.add_subplot(212)
        ax2.plot(df["Date"], df["Recovery_Score"], color="#17a2b8", linewidth=2, label="Recovery Score (0-100)")
        ax2.axhline(50, color="orange", linestyle=":", label="Low Recovery Warning (<50)")

        ax2.set_ylabel("Recovery Score")
        ax2.set_xlabel("Date")
        ax2.set_title("Perceived Recovery Score Trend")
        ax2.grid(True, linestyle=":", alpha=0.6)
        ax2.legend(loc="upper left")

        self.fig_trends.tight_layout()
        self.canvas_trends.draw()

    # -------------------------------------------------------------------------
    # TABLE MANAGEMENT
    # -------------------------------------------------------------------------

    def update_table(self, df):
        cols = ["Date", "Athlete", "Daily_Load", "Weekly_Load", "Rolling_7d_Acute", "Rolling_28d_Chronic", "ACWR", "Monotony", "Strain", "Recovery_Score"]
        display_df = df[cols].sort_values("Date", ascending=False)

        self.data_table.setRowCount(len(display_df))
        self.data_table.setColumnCount(len(cols))
        self.data_table.setHorizontalHeaderLabels(["Date", "Athlete", "Daily Load", "Weekly Load", "Acute (7d)", "Chronic (28d)", "ACWR", "Monotony", "Strain", "Recovery"])

        for row_idx, (_, row) in enumerate(display_df.iterrows()):
            self.data_table.setItem(row_idx, 0, QTableWidgetItem(row["Date"].strftime("%Y-%m-%d")))
            self.data_table.setItem(row_idx, 1, QTableWidgetItem(str(row["Athlete"])))
            self.data_table.setItem(row_idx, 2, QTableWidgetItem(f"{row['Daily_Load']:.0f}"))
            self.data_table.setItem(row_idx, 3, QTableWidgetItem(f"{row['Weekly_Load']:.0f}"))
            self.data_table.setItem(row_idx, 4, QTableWidgetItem(f"{row['Rolling_7d_Acute']:.0f}"))
            self.data_table.setItem(row_idx, 5, QTableWidgetItem(f"{row['Rolling_28d_Chronic']:.0f}"))
            self.data_table.setItem(row_idx, 6, QTableWidgetItem(f"{row['ACWR']:.2f}"))
            self.data_table.setItem(row_idx, 7, QTableWidgetItem(f"{row['Monotony']:.2f}"))
            self.data_table.setItem(row_idx, 8, QTableWidgetItem(f"{row['Strain']:.0f}"))
            self.data_table.setItem(row_idx, 9, QTableWidgetItem(f"{row['Recovery_Score']:.1f}"))

        self.data_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

    # -------------------------------------------------------------------------
    # EXPORT FUNCTIONS
    # -------------------------------------------------------------------------

    def export_csv(self):
        df = self.get_filtered_df()
        file_path, _ = QFileDialog.getSaveFileName(self, "Export Load Data CSV", "Athlete_Load_Data.csv", "CSV Files (*.csv)")
        if file_path:
            df.to_csv(file_path, index=False)
            QMessageBox.information(self, "Export Successful", f"Data exported successfully to:\n{file_path}")

    def export_pdf(self):
        file_path, _ = QFileDialog.getSaveFileName(self, "Export Load Diagnostic PDF", "Athlete_Load_Report.pdf", "PDF Files (*.pdf)")
        if not file_path:
            return

        try:
            temp_dir = tempfile.gettempdir()
            loads_img_path = os.path.join(temp_dir, "temp_loads_chart.png")
            trends_img_path = os.path.join(temp_dir, "temp_trends_chart.png")
            
            self.fig_loads.savefig(loads_img_path, dpi=150)
            self.fig_trends.savefig(trends_img_path, dpi=150)

            doc = SimpleDocTemplate(file_path, pagesize=letter, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
            styles = getSampleStyleSheet()

            title_style = ParagraphStyle('DocTitle', parent=styles['Heading1'], fontSize=18, leading=22, textColor=colors.HexColor('#003366'))
            sub_style = ParagraphStyle('SubTitle', parent=styles['Normal'], fontSize=9, leading=12, textColor=colors.HexColor('#555555'))
            body_style = ParagraphStyle('BodyCustom', parent=styles['Normal'], fontSize=9, leading=13)

            story = []

            # Document Title
            story.append(Paragraph("Athlete Load Management Diagnostic Report", title_style))
            story.append(Paragraph(f"Subject: <b>{self.current_athlete}</b> | Generated: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}", sub_style))
            story.append(Spacer(1, 10))

            # Summary Table
            df = self.get_filtered_df()
            latest = df.iloc[-1]
            summary_data = [
                ["Metric", "Latest Value", "Status / Reference"],
                ["Acute Load (7d)", f"{latest['Rolling_7d_Acute']:.0f} AU", "Recent fatigue accumulation"],
                ["Chronic Load (28d)", f"{latest['Rolling_28d_Chronic']:.0f} AU", "Historical fitness baseline"],
                ["ACWR", f"{latest['ACWR']:.2f}", "Optimal Range: 0.8 - 1.3"],
                ["Monotony", f"{latest['Monotony']:.2f}", "High Risk Threshold: > 2.0"],
                ["Strain", f"{latest['Strain']:.0f} AU", "High Risk Threshold: > 6000 AU"],
                ["Recovery Score", f"{latest['Recovery_Score']:.1f}", "Warning Threshold: < 50"]
            ]

            t_summary = Table(summary_data, colWidths=[130, 120, 250])
            t_summary.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#003366')),
                ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
                ('ALIGN', (0,0), (-1,-1), 'LEFT'),
                ('GRID', (0,0), (-1,-1), 0.5, colors.lightgrey),
            ]))
            story.append(t_summary)
            story.append(Spacer(1, 15))

            # Visualizations
            story.append(Paragraph("<b>1. Workload Accumulation Charts</b>", body_style))
            story.append(Spacer(1, 5))
            story.append(Image(loads_img_path, width=520, height=220))
            story.append(Spacer(1, 10))

            story.append(Paragraph("<b>2. Longitudinal ACWR & Recovery Trends</b>", body_style))
            story.append(Spacer(1, 5))
            story.append(Image(trends_img_path, width=520, height=220))

            doc.build(story)

            # Cleanup Temp Images
            for path in [loads_img_path, trends_img_path]:
                if os.path.exists(path):
                    os.remove(path)

            QMessageBox.information(self, "Export Complete", f"PDF report successfully created:\n{file_path}")

        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to generate PDF report: {str(e)}")


# -----------------------------------------------------------------------------
# APPLICATION ENTRY POINT
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())