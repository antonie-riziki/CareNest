# CareNest

CareNest is a centralized platform designed to seamlessly connect domestic workers with employers, providing a safe, reliable, and efficient ecosystem for job discovery, hiring, and management.

## 🚀 Features

### For Domestic Workers
* **Job Discovery (Map-based):** A location-aware job discovery interface displaying nearby opportunities using an interactive map, and filtering jobs based on a proximity radius.
* **Profiles & Reputation Management:** A polished profile showcasing verified skills, detailed ratings, core expertise badges, and client testimonials.
* **Dashboard & Earnings:** A centralized hub to view cumulative daily revenue, check active duty status, and quickly access operational toolkits.
* **Wallet Analytics:** Keep track of payments, reliability metrics, and completion data.
* **Growth & Upskilling Hub:** Access premium professional courses to expand skillsets in areas like housekeeping, culinary arts, child care, and more.

### Platform Architecture
* **Responsive Design:** Mobile-first architecture for workers on the go, paired with a unified sidebar and split-screen interface crafted specifically for larger desktop environments.
* **USSD Integration:** Alternative USSD application pathway designed for accessibility and offline usability in low connectivity scenarios.

## 🛠 Tech Stack

* **Backend:** [Django](https://www.djangoproject.com/) Application Framework
* **Frontend:** HTML Interface, [Tailwind CSS](https://tailwindcss.com/)
* **Mapping:** [Leaflet.js](https://leafletjs.com/) for real-time location discovery
* **Icons & Assets:** Google Material Symbols (Outlined)

## 💻 Local Development Setup

To run the CareNest application locally, follow these steps:

1. **Clone the repository:**
   ```bash
   git clone <repository-url>
   cd CareNest/Care_Nest
   ```

2. **Set up a Virtual Environment (Recommended):**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

3. **Install Requirements:**
   *(Ensure you have Django and required dependencies installed.)*
   ```bash
   pip install -r requirements.txt
   ```

4. **Run Migrations:**
   ```bash
   python3 manage.py migrate
   ```

5. **Start the Development Server:**
   ```bash
   python3 manage.py runserver
   ```

6. Open your browser and navigate to `http://127.0.0.1:8000`.

## 🤝 Project Structure
- `templates/`: Contains all our mobile-responsive HTML templates (Dashboards, Maps, Profiles).
- `apps/ussd_app/`: Micro-service handling USSD menus for basic phone users.

## 📄 Organization
© 2026 Care Nest Kenya. All Rights Reserved.
