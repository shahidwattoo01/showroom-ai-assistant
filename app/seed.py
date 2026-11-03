"""Temporary demo data so the bot works out of the box.

All prices / stock here are SAMPLE values. Replace them with the real showroom
data through the admin panel or the Excel import (Import / Export page).
"""
from datetime import date, timedelta

from .config import settings
from .db import now, session_scope
from .models import (CarModel, Customer, CustomerVehicle, Lead, Offer, Part, ServiceAppointment, ServiceType,
                     Staff, TestDrive, Vehicle)
from .utils import hash_password

CATALOG = [
    # make, model, variant, body, fuel, transmission, cc, seats, kmpl, price, features
    ("Honda", "Civic", "Oriel 1.5 Turbo", "sedan", "petrol", "CVT", 1498, 5, 13, 9_449_000,
     "Turbo engine, sunroof, Honda Sensing, 9-inch touchscreen, leather seats, 6 airbags"),
    ("Honda", "Civic", "RS 1.5 Turbo", "sedan", "petrol", "CVT", 1498, 5, 12.5, 9_999_000,
     "RS body kit, Bose audio, LED headlights, Honda Sensing, wireless Apple CarPlay"),
    ("Honda", "City", "1.5 Aspire CVT", "sedan", "petrol", "CVT", 1497, 5, 15, 5_399_000,
     "Push start, alloy wheels, touchscreen, cruise control, 2 airbags"),
    ("Honda", "HR-V", "VTi-S", "crossover", "petrol", "CVT", 1497, 5, 14, 7_399_000,
     "Panoramic glass roof option, Honda Sensing, 360 camera, LED lights"),
    ("Toyota", "Corolla", "Altis 1.6 X CVT", "sedan", "petrol", "CVT", 1598, 5, 13, 7_169_000,
     "7 airbags option, push start, 8-inch touchscreen, LED DRLs"),
    ("Toyota", "Corolla", "Altis Grande 1.8 CVT", "sedan", "petrol", "CVT", 1798, 5, 12, 7_969_000,
     "Leather seats, sunroof, cruise control, 7 airbags, rear camera"),
    ("Toyota", "Yaris", "ATIV X CVT 1.5", "sedan", "petrol", "CVT", 1496, 5, 16, 5_479_000,
     "Push start, touchscreen, 7 airbags, LED headlights"),
    ("Toyota", "Corolla Cross", "1.8 HEV", "crossover", "hybrid", "CVT", 1798, 5, 21, 10_999_000,
     "Self-charging hybrid, Toyota Safety Sense, 360 camera, power tailgate"),
    ("Toyota", "Fortuner", "Legender 2.8 Diesel", "suv", "diesel", "Automatic", 2755, 7, 10, 17_399_000,
     "4x4, 7 seats, ventilated seats, JBL audio, power tailgate"),
    ("Suzuki", "Alto", "VXL AGS", "hatchback", "petrol", "AGS", 658, 4, 22, 3_225_000,
     "Auto gear shift, airbags, power windows, economical city car"),
    ("Suzuki", "Cultus", "VXL AGS", "hatchback", "petrol", "AGS", 998, 5, 20, 4_150_000,
     "Auto gear shift, ABS, touchscreen, alloy wheels"),
    ("Suzuki", "Swift", "GLX CVT", "hatchback", "petrol", "CVT", 1197, 5, 18, 5_046_000,
     "Sporty hatch, 360 camera, push start, alloy wheels"),
    ("KIA", "Sportage", "AWD 2.0", "suv", "petrol", "Automatic", 1999, 5, 11, 9_850_000,
     "AWD, panoramic sunroof, ventilated seats, smart key"),
]

UPCOMING = [
    ("Honda", "Civic", "e:HEV Hybrid", "sedan", "hybrid", "e-CVT", 1993, 5, 22, 11_500_000,
     "Two-motor hybrid, Honda Sensing 360, Bose audio", 60),
    ("Toyota", "Yaris Cross", "1.5 HEV", "crossover", "hybrid", "e-CVT", 1490, 5, 24, 8_900_000,
     "Compact hybrid crossover, Toyota Safety Sense, digital cluster", 95),
]

PARTS = [
    # part_no, name, category, compatible, price, stock
    ("HN-BP-CV22", "Front brake pads", "brakes", "Honda Civic 2022-2025", 18_500, 12),
    ("HN-BP-CT21", "Front brake pads", "brakes", "Honda City 2021-2025", 11_200, 3),
    ("TY-BP-CR20", "Front brake pads", "brakes", "Toyota Corolla 2020-2025; Toyota Yaris 2020-2025", 12_800, 8),
    ("SZ-BP-AL19", "Front brake pads", "brakes", "Suzuki Alto 2019-2025", 4_900, 20),
    ("HN-OF-15T", "Oil filter", "filters", "Honda Civic 2022-2025; Honda HR-V 2022-2025", 2_400, 40),
    ("HN-OF-15", "Oil filter", "filters", "Honda City 2021-2025", 1_900, 35),
    ("TY-OF-16", "Oil filter", "filters", "Toyota Corolla 2018-2025; Toyota Yaris 2020-2025", 1_850, 50),
    ("SZ-OF-K10", "Oil filter", "filters", "Suzuki Alto 2019-2025; Suzuki Cultus 2017-2025", 950, 60),
    ("HN-AF-CV22", "Air filter", "filters", "Honda Civic 2022-2025", 4_800, 10),
    ("TY-AF-CR20", "Air filter", "filters", "Toyota Corolla 2020-2025", 3_900, 6),
    ("TY-CF-CR20", "AC cabin filter", "filters", "Toyota Corolla 2020-2025; Toyota Yaris 2020-2025", 2_900, 15),
    ("HN-CF-CV22", "AC cabin filter", "filters", "Honda Civic 2022-2025; Honda City 2021-2025", 3_100, 0),
    ("GEN-OIL-0W20", "Engine oil 0W-20 (4L)", "oil", "Honda Civic; Honda City; Toyota Corolla; Toyota Yaris; Corolla Cross", 14_500, 30),
    ("GEN-OIL-5W30", "Engine oil 5W-30 (3L)", "oil", "Suzuki Alto; Suzuki Cultus; Suzuki Swift", 8_200, 25),
    ("HN-WB-CV22", "Wiper blades (pair)", "body", "Honda Civic 2022-2025", 5_600, 9),
    ("TY-WB-CR20", "Wiper blades (pair)", "body", "Toyota Corolla 2020-2025", 4_700, 11),
    ("HN-HL-CT21", "Headlight assembly (left)", "lights", "Honda City 2021-2025", 78_000, 1),
    ("TY-HL-CR20", "Headlight assembly (right)", "lights", "Toyota Corolla 2020-2025", 92_000, 0),
    ("HN-SM-CV22", "Side mirror (right, auto-fold)", "body", "Honda Civic 2022-2025", 64_000, 2),
    ("TY-BAT-55", "Battery 55Ah", "electrical", "Toyota Corolla; Toyota Yaris; Honda City; Honda Civic", 32_000, 7),
    ("SZ-BAT-35", "Battery 35Ah", "electrical", "Suzuki Alto; Suzuki Cultus; Suzuki Swift", 19_500, 9),
    ("TY-SP-16", "Spark plugs (set of 4)", "engine", "Toyota Corolla 1.6/1.8; Toyota Yaris 1.3/1.5", 9_600, 14),
    ("HN-SP-15T", "Spark plugs (set of 4)", "engine", "Honda Civic 1.5 Turbo", 16_800, 5),
    ("ACC-MAT-UNI", "3D floor mats", "accessories", "Most sedans and hatchbacks", 12_500, 18),
    ("ACC-CAM-360", "360 camera kit", "accessories", "Universal (installation extra)", 85_000, 3),
]

SERVICE_TYPES = [
    ("Periodic maintenance (every 5,000 km)", "Engine oil + oil filter change, 30-point inspection, wash", 9_000, 18_000, 2),
    ("Major service (every 20,000 km)", "Oil, all filters, spark plugs, brake service, full inspection", 28_000, 45_000, 4),
    ("Brake service", "Brake pad replacement and disc check (parts extra)", 3_000, 6_000, 1.5),
    ("AC service", "AC gas top-up, cabin filter, cooling check", 6_500, 12_000, 2),
    ("Wheel alignment & balancing", "Computerised alignment and balancing of 4 wheels", 3_500, 5_000, 1),
    ("Diagnostics / warning light check", "Computer scan and inspection of the reported issue", 2_500, 4_000, 1),
    ("Body & paint (per panel)", "Dent repair and paint per panel", 15_000, 30_000, 24),
]


def _cm(db, make, model, variant):
    return db.query(CarModel).filter_by(make=make, model=model, variant=variant).first()


def seed_admin(db):
    if not db.query(Staff).filter_by(username=settings.admin_username).first():
        db.add(Staff(name="Showroom Owner", username=settings.admin_username,
                     password_hash=hash_password(settings.admin_password), phone="923000000001", role="owner"))


def seed_demo(db):
    today = now().date()
    for row in CATALOG:
        db.add(CarModel(make=row[0], model=row[1], variant=row[2], body_type=row[3], fuel=row[4],
                        transmission=row[5], engine_cc=row[6], seats=row[7], fuel_avg_kmpl=row[8],
                        price=row[9], features=row[10]))
    for row in UPCOMING:
        db.add(CarModel(make=row[0], model=row[1], variant=row[2], body_type=row[3], fuel=row[4],
                        transmission=row[5], engine_cc=row[6], seats=row[7], fuel_avg_kmpl=row[8],
                        price=row[9], features=row[10], is_upcoming=True, launch_date=today + timedelta(days=row[11])))
    db.flush()

    stock = [
        # make, model, variant, condition, year, color, mileage, extra price delta, count
        ("Honda", "Civic", "Oriel 1.5 Turbo", "new", 2025, "White", 0, 0, 2),
        ("Honda", "Civic", "Oriel 1.5 Turbo", "new", 2025, "Black", 0, 0, 1),
        ("Honda", "Civic", "RS 1.5 Turbo", "new", 2025, "Grey", 0, 0, 1),
        ("Honda", "City", "1.5 Aspire CVT", "new", 2025, "White", 0, 0, 3),
        ("Honda", "City", "1.5 Aspire CVT", "new", 2025, "Silver", 0, 0, 1),
        ("Honda", "HR-V", "VTi-S", "new", 2025, "Red", 0, 0, 1),
        ("Toyota", "Corolla", "Altis 1.6 X CVT", "new", 2025, "Grey", 0, 0, 2),
        ("Toyota", "Corolla", "Altis Grande 1.8 CVT", "new", 2025, "White", 0, 0, 2),
        ("Toyota", "Yaris", "ATIV X CVT 1.5", "new", 2025, "Silver", 0, 0, 2),
        ("Toyota", "Corolla Cross", "1.8 HEV", "new", 2025, "Pearl White", 0, 0, 1),
        ("Toyota", "Fortuner", "Legender 2.8 Diesel", "new", 2025, "Black", 0, 0, 1),
        ("Suzuki", "Alto", "VXL AGS", "new", 2025, "White", 0, 0, 4),
        ("Suzuki", "Cultus", "VXL AGS", "new", 2025, "Blue", 0, 0, 2),
        ("Suzuki", "Swift", "GLX CVT", "new", 2025, "Red", 0, 0, 1),
        ("KIA", "Sportage", "AWD 2.0", "new", 2025, "Grey", 0, 0, 1),
        # used cars (price = negative delta from new price)
        ("Honda", "Civic", "Oriel 1.5 Turbo", "used", 2022, "White", 48_000, -2_350_000, 1),
        ("Honda", "City", "1.5 Aspire CVT", "used", 2021, "Grey", 62_000, -1_650_000, 1),
        ("Toyota", "Corolla", "Altis 1.6 X CVT", "used", 2020, "White", 85_000, -2_100_000, 1),
        ("Toyota", "Yaris", "ATIV X CVT 1.5", "used", 2022, "Silver", 35_000, -1_150_000, 1),
        ("Suzuki", "Alto", "VXL AGS", "used", 2021, "Red", 41_000, -850_000, 1),
        ("Suzuki", "Cultus", "VXL AGS", "used", 2019, "White", 77_000, -1_350_000, 1),
    ]
    n = 1000
    for make, model, variant, cond, year, color, km, delta, count in stock:
        cm = _cm(db, make, model, variant)
        for i in range(count):
            n += 1
            db.add(Vehicle(car_model_id=cm.id, condition=cond, year=year, color=color, price=cm.price + delta,
                           mileage_km=km, chassis_no=f"{make[:2].upper()}{model[:3].upper()}{n}",
                           reg_no=(f"LE{20 + i}-{n}" if cond == "used" else None),
                           arrived_at=today - timedelta(days=(n % 30))))
    # a couple of sold units for history
    civic = _cm(db, "Honda", "Civic", "Oriel 1.5 Turbo")
    db.add(Vehicle(car_model_id=civic.id, condition="new", year=2025, color="White", price=civic.price,
                   chassis_no="HOCIV0999", status="sold", arrived_at=today - timedelta(days=40)))

    for part_no, name, cat, compat, price, stock_qty in PARTS:
        db.add(Part(part_no=part_no, name=name, category=cat, compatible_models=compat, price=price,
                    stock=stock_qty, min_stock=2))
    for name, desc, pmin, pmax, hours in SERVICE_TYPES:
        db.add(ServiceType(name=name, description=desc, price_min=pmin, price_max=pmax, duration_hours=hours))

    db.add(Offer(title="Free first periodic service", description="Free labour on the first 5,000 km service for every new car booked this month.",
                 valid_from=today.replace(day=1), valid_to=today + timedelta(days=30)))
    db.add(Offer(title="Bank financing at reduced markup", description="Partner bank financing with 30% down payment and up to 5 years tenure. Processing fee waived.",
                 valid_from=today - timedelta(days=5), valid_to=today + timedelta(days=45)))
    db.add(Offer(title="Trade-in bonus", description="Extra PKR 50,000 on top of the valuation when you trade in your old car for a new Honda or Toyota.",
                 valid_from=today - timedelta(days=10), valid_to=today + timedelta(days=20)))

    # staff who can update data via WhatsApp (demo numbers)
    db.add(Staff(name="Ahmed (Sales Manager)", username="ahmed", password_hash=hash_password("ahmed123"),
                 phone="923001112233", role="sales"))
    db.add(Staff(name="Bilal (Parts Incharge)", username="bilal", password_hash=hash_password("bilal123"),
                 phone="923214445566", role="parts"))
    db.add(Staff(name="Usman (Service Advisor)", username="usman", password_hash=hash_password("usman123"),
                 phone="923337778899", role="service"))

    # demo customers with history
    c1 = Customer(name="Ali Raza", phone="923451234567", city="Lahore", memory="Owns a 2022 Honda City. Interested in upgrading to Civic.")
    c2 = Customer(name="Sara Khan", phone="923009876543", city="Lahore", memory="Owns a 2020 Corolla Altis. Prefers Saturday appointments.")
    c3 = Customer(name="Hamza Sheikh", phone="923111222333", city="Lahore")
    db.add_all([c1, c2, c3])
    db.flush()
    db.add(CustomerVehicle(customer_id=c1.id, reg_no="LEB-22-4521", model_name="Honda City 1.5 Aspire", year=2022,
                           last_service_date=today - timedelta(days=170), last_service_km=30_000,
                           next_service_due=today + timedelta(days=2)))
    db.add(CustomerVehicle(customer_id=c2.id, reg_no="LEA-20-7789", model_name="Toyota Corolla Altis 1.6", year=2020,
                           last_service_date=today - timedelta(days=60), last_service_km=84_000,
                           next_service_due=today + timedelta(days=120)))

    start = now().replace(minute=0, second=0)
    db.add(ServiceAppointment(job_no="JOB-1041", customer_id=c2.id, reg_no="LEA-20-7789", car_model="Toyota Corolla Altis 1.6",
                              service_type="AC service", scheduled_at=start.replace(hour=10), status="in_progress", source="walk-in"))
    db.add(ServiceAppointment(job_no="JOB-1042", customer_id=c3.id, reg_no="LEC-23-1188", car_model="Suzuki Cultus VXL",
                              service_type="Periodic maintenance (every 5,000 km)", scheduled_at=start.replace(hour=11),
                              status="ready", bill_amount=11_800, source="walk-in"))
    db.add(ServiceAppointment(job_no="JOB-1043", customer_id=c1.id, reg_no="LEB-22-4521", car_model="Honda City 1.5 Aspire",
                              service_type="Periodic maintenance (every 5,000 km)",
                              scheduled_at=(start + timedelta(days=1)).replace(hour=12), status="booked", source="phone"))
    db.add(TestDrive(customer_id=c1.id, car_model_id=civic.id,
                     scheduled_at=(start + timedelta(days=1)).replace(hour=15), location="showroom"))
    db.add(Lead(customer_id=c1.id, interest="Honda Civic Oriel, white", car_model_id=civic.id, budget=9_500_000,
                timeline="this month", wants_financing=True, has_trade_in=True, source="whatsapp",
                status="test_drive", score=85, score_label="hot"))
    db.add(Lead(customer_id=c3.id, interest="Family SUV under 1 crore", budget=10_000_000, source="web",
                status="new", score=25, score_label="cold"))


def run_seed():
    with session_scope() as db:
        seed_admin(db)
        if settings.seed_demo_data and db.query(CarModel).count() == 0:
            seed_demo(db)
