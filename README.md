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

## 🧱 System Architecture

CareNest is built on a highly available, robust, and scalable architecture that ensures zero-downtime, fault-tolerance, and lightning-fast execution. The system seamlessly unifies a traditional robust backend with bleeding-edge Web3 decentralized infrastructure.

### High-Performance Infrastructure
* **Distributed Backend:** Powered by Django, our core API interfaces effortlessly handle high-volume concurrent requests, ensuring data integrity, rapid response times, and an unbreachable security perimeter.
* **Real-time Event-Driven Busses:** Mission-critical updates, such as job matching and immediate proximity alerts, are processed to guarantee sub-second latency across the network.
* **Persistent Data Layer:** Employs advanced relational databases for user and administrative data, seamlessly harmonized with high-velocity caching layers for ephemeral data like live rapid GPS tracking streams.

## ⚡ Smart Contract & Web3 Integration

CareNest transcends traditional gig-economy platforms by heavily integrating decentralized Web3 protocols to guarantee immutable trust, irrefutable reputation, and programmatic, trustless escrow mechanisms.

### Core Decentralized Components
* **Automated Escrow Agreements:** Every service engagement on CareNest orchestrates a smart contract on a high-throughput blockchain network. Funds are programmatically and cryptographically locked before the job commences and automatically released upon verifiable mathematical completion based on predetermined consensus.
* **Immutable Reputation System (Soulbound Tokens):** Worker ratings, verified skills, and rigorous background-check certifications are minted as non-transferable Soulbound Tokens (SBTs) directly linked to the worker's decentralized identity. This constructs a cryptographic, tamper-proof professional history that is permanently verifiable and impervious to manipulation.
* **Decentralized Dispute Resolution:** In the event of a dispute, smart contracts algorithmically trigger deterministic fallback logic and multi-signature arbitration, resolving conflicts securely and transparently without centralized bias points.
* **Zero-Friction Transaction Abstraction:** Through robust meta-transaction relayers, end-users interact with complex smart contracts completely gas-free. CareNest entirely abstracts the underlying blockchain cryptographic complexity, ensuring a seamless, high-velocity user experience indistinguishable from traditional platforms.

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
