"""
Clinical Physiology Calculator
A command-line tool for calculating key physiological metrics and health indicators.

Author: Simeon Paul Leeleebari
Date: 2026
"""

import csv
import json
from datetime import datetime
from pathlib import Path


def get_user_inputs() -> dict:
    """
    Collect user inputs for physiological calculations.
    
    Prompts the user for: Name, Sex, Age, Weight (kg), Height (cm), Activity Level, and Resting Heart Rate (BPM).
    Validates that numeric inputs are positive values.
    
    Returns:
        dict: A dictionary containing 'name', 'age', 'weight', 'height', and 'resting_hr'.
        
    Raises:
        ValueError: If non-numeric values are entered for numeric fields.
        ValueError: If negative or zero values are provided for numeric fields.
    """
    print("\n" + "="*60)
    print("CLINICAL PHYSIOLOGY CALCULATOR")
    print("="*60 + "\n")
    
    try:
        # Get name
        name = input("Enter your name: ").strip()
        if not name:
            raise ValueError("Name cannot be empty.")
        
        # Get age
        while True:
            try:
                age = int(input("Enter your age (years): "))
                if age <= 0 or age > 150:
                    print("Please enter a valid age (1-150 years).")
                    continue
                break
            except ValueError:
                print("Error: Please enter a valid integer for age.")
        
        # Get weight
        while True:
            try:
                weight = float(input("Enter your weight (kg): "))
                if weight <= 0:
                    print("Please enter a positive value for weight.")
                    continue
                break
            except ValueError:
                print("Error: Please enter a valid number for weight.")
        
        # Get height
        while True:
            try:
                height = float(input("Enter your height (cm): "))
                if height <= 0:
                    print("Please enter a positive value for height.")
                    continue
                break
            except ValueError:
                print("Error: Please enter a valid number for height.")

        # Get sex
        while True:
            sex = input("Enter your sex (male/female): ").strip().lower()
            if sex in ("male", "m"):
                sex = "male"
                break
            elif sex in ("female", "f"):
                sex = "female"
                break
            else:
                print("Please enter 'male' or 'female'.")

        # Get activity level
        activity_level_mapping = {
            '1': "Sedentary",
            '2': "Lightly active",
            '3': "Moderately active",
            '4': "Very active",
            '5': "Extra active"
        }
        print("\nSelect your activity level:")
        print("1. Sedentary (little or no exercise)")
        print("2. Lightly active (light exercise 1-3 days/week)")
        print("3. Moderately active (moderate exercise 3-5 days/week)")
        print("4. Very active (hard exercise 6-7 days/week)")
        print("5. Extra active (very hard exercise / physical job)")
        while True:
            activity_choice = input("Enter the number for your activity level: ").strip()
            if activity_choice in activity_level_mapping:
                activity_level = activity_level_mapping[activity_choice]
                break
            print("Please enter a valid choice between 1 and 5.")

        # Get resting heart rate
        while True:
            try:
                resting_hr = int(input("Enter your resting heart rate (BPM): "))
                if resting_hr <= 0 or resting_hr > 200:
                    print("Please enter a valid resting heart rate (1-200 BPM).")
                    continue
                break
            except ValueError:
                print("Error: Please enter a valid integer for heart rate.")
        
        return {
            'name': name,
            'sex': sex,
            'age': age,
            'weight': weight,
            'height': height,
            'activity_level': activity_level,
            'resting_hr': resting_hr
        }
    
    except KeyboardInterrupt:
        print("\n\nCalculation cancelled by user.")
        exit()


def calculate_bmi(weight: float, height: float) -> float:
    """
    Calculate Body Mass Index (BMI).
    
    Formula: BMI = weight (kg) / [height (m)]^2
    
    Args:
        weight (float): Body weight in kilograms.
        height (float): Height in centimeters.
        
    Returns:
        float: The calculated BMI value.
    """
    height_m = height / 100  # Convert cm to meters
    bmi = weight / (height_m ** 2)
    return bmi


def get_bmi_category(bmi: float) -> str:
    """
    Determine BMI clinical category.
    
    Categories:
        - Underweight: BMI < 18.5
        - Normal weight: 18.5 <= BMI < 25
        - Overweight: 25 <= BMI < 30
        - Obese: BMI >= 30
    
    Args:
        bmi (float): Body Mass Index value.
        
    Returns:
        str: The clinical category corresponding to the BMI.
    """
    if bmi < 18.5:
        return "Underweight"
    elif bmi < 25:
        return "Normal weight"
    elif bmi < 30:
        return "Overweight"
    else:
        return "Obese"


def calculate_bmr(age: int, weight: float, height: float, sex: str) -> float:
    """
    Calculate Basal Metabolic Rate (BMR) using the Mifflin-St Jeor Equation.
    Supports male and female formulas.
    
    Formulas:
        Male:   BMR = (10 * weight) + (6.25 * height) - (5 * age) + 5
        Female: BMR = (10 * weight) + (6.25 * height) - (5 * age) - 161
    
    Args:
        age (int): Age in years.
        weight (float): Body weight in kilograms.
        height (float): Height in centimeters.
        sex (str): 'male' or 'female'.
        
    Returns:
        float: The calculated BMR in kcal/day.
    """
    offset = -161 if sex == "female" else 5

    bmr = (10 * weight) + (6.25 * height) - (5 * age) + offset
    return bmr


def calculate_tdee(bmr: float, activity_level: str) -> float:
    """
    Calculate Total Daily Energy Expenditure (TDEE) based on activity level.
    
    Args:
        bmr (float): Basal Metabolic Rate in kcal/day.
        activity_level (str): Activity level string.
        
    Returns:
        float: The estimated TDEE in kcal/day.
    """
    activity_factor = {
        "Sedentary": 1.2,
        "Lightly active": 1.375,
        "Moderately active": 1.55,
        "Very active": 1.725,
        "Extra active": 1.9,
    }
    factor = activity_factor.get(activity_level)
    if factor is None:
        raise ValueError("Unsupported activity level for TDEE calculation.")
    return bmr * factor


def calculate_max_heart_rate(age: int) -> int:
    """
    Calculate maximum heart rate using the age-predicted formula.
    
    Formula: Max HR = 220 - Age
    
    Args:
        age (int): Age in years.
        
    Returns:
        int: The estimated maximum heart rate in BPM.
    """
    return 220 - age


def calculate_target_heart_rate_zones(age: int, resting_hr: int) -> dict:
    """
    Calculate target heart rate zones using the Karvonen Formula.
    
    Formula: Target HR = ((Max HR - Resting HR) * %Intensity) + Resting HR
    
    Calculates both 60% (moderate intensity) and 80% (vigorous intensity) zones.
    
    Args:
        age (int): Age in years.
        resting_hr (int): Resting heart rate in BPM.
        
    Returns:
        dict: Contains 'max_hr', 'zone_60', and 'zone_80' (60% and 80% intensity zones).
    """
    max_hr = calculate_max_heart_rate(age)
    heart_rate_reserve = max_hr - resting_hr

    zone_60 = round((heart_rate_reserve * 0.60) + resting_hr)
    zone_80 = round((heart_rate_reserve * 0.80) + resting_hr)

    return {
        "max_hr": max_hr,
        "zone_60": zone_60,
        "zone_80": zone_80,
    }


def build_health_recommendations(
    bmi: float,
    bmr: float,
    age: int,
    sex: str,
    resting_hr: int,
    activity_level: str,
) -> dict:
    """
    Build a set of tailored wellness recommendations based on core metrics.
    """
    bmi_category = get_bmi_category(bmi)
    tdee = calculate_tdee(bmr, activity_level)
    deficit = round(max(300, min(650, tdee * 0.15)))
    daily_calories = round(tdee - deficit)

    if bmi >= 30:
        cardio_plan = "Start with low-impact cardio, such as brisk walking, stationary cycling, or aqua therapy."
    elif bmi >= 25:
        cardio_plan = "Use low-impact cardio plus moderate interval walking to lower body fat while protecting joints."
    elif bmi >= 18.5:
        cardio_plan = "Maintain moderate cardiovascular training 3-5 times per week with recovery days built in."
    else:
        cardio_plan = "Focus on gentle strength training and progressive conditioning to support healthy weight gain."

    if bmi >= 25:
        nutrition_plan = (
            f"Aim for a caloric deficit of about {deficit} kcal/day, targeting {daily_calories} kcal/day "
            "with lean proteins, fiber, and nutrient-dense vegetables."
        )
    else:
        nutrition_plan = (
            f"Maintain your energy needs with around {round(tdee)} kcal/day, prioritizing whole foods, "
            "healthy fats, and consistent hydration."
        )

    if resting_hr > 75:
        recovery_note = "Your resting heart rate is above typical resting range. Build recovery and relaxation sessions into each week."
    else:
        recovery_note = "Your resting heart rate is in a healthy range; continue structured recovery and mobility work."

    return {
        "bmi_category": bmi_category,
        "tdee": round(tdee),
        "daily_calories": daily_calories,
        "cardio_plan": cardio_plan,
        "nutrition_plan": nutrition_plan,
        "recovery_note": recovery_note,
    }


if __name__ == "__main__":
    inputs = get_user_inputs()
    bmi = calculate_bmi(inputs["weight"], inputs["height"])
    bmr = calculate_bmr(inputs["age"], inputs["weight"], inputs["height"], inputs["sex"])
    hr_zones = calculate_target_heart_rate_zones(inputs["age"], inputs["resting_hr"])

    print(f"\nBMI: {bmi:.1f}")
    print(f"BMR: {bmr:.0f} kcal/day")
    print(f"Target HR 60%: {hr_zones['zone_60']} bpm")
    print(f"Target HR 80%: {hr_zones['zone_80']} bpm")
