"""
Clinical Physiology Calculator - Web UI
A Flask web application for calculating key physiological metrics and health indicators.

Author: Simeon Paul Leeleebari
Date: 2026
"""

from flask import Flask, render_template, request, jsonify
from home import (
    calculate_bmi, get_bmi_category, calculate_bmr, calculate_target_heart_rate_zones
)

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/calculate', methods=['POST'])
def calculate():
    data = request.get_json()

    name = data.get('name', '')
    sex = data.get('sex', '').lower()
    age = int(data.get('age', 0))
    weight = float(data.get('weight', 0))
    height = float(data.get('height', 0))
    resting_hr = int(data.get('resting_hr', 0))

    if not all([name, sex, age, weight, height, resting_hr]):
        return jsonify({'error': 'All fields are required'}), 400

    # Calculations
    bmi = calculate_bmi(weight, height)
    bmi_category = get_bmi_category(bmi)
    bmr = calculate_bmr(age, weight, height, sex)
    hr_zones = calculate_target_heart_rate_zones(age, resting_hr)

    result = {
        'name': name,
        'bmi': round(bmi, 1),
        'bmi_category': bmi_category,
        'bmr': round(bmr),
        'max_hr': hr_zones['max_hr'],
        'zone_60': hr_zones['zone_60'],
        'zone_80': hr_zones['zone_80']
    }

    return jsonify(result)

if __name__ == '__main__':
    app.run(debug=True)