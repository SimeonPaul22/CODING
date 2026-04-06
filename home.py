"""
Clinical Physiology Calculator
A command-line tool for calculating key physiological metrics and health indicators.

Author: Simeon Paul Leeleebari
Date: 2026
"""


def get_user_inputs() -> dict:
    """
    Collect user inputs for physiological calculations.
    
    Prompts the user for: Name, Age, Weight (kg), Height (cm), and Resting Heart Rate (BPM).
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
            'age': age,
            'weight': weight,
            'height': height,
            'resting_hr': resting_hr
        }
    
    except KeyboardInterrupt:
        print("\n\nCalculation cancelled by user.")
        exit()


def calculate_bmi(weight: float, height: float) -> float:
    """
    Calculate Body Mass Index (BMI).
    
    Formula: BMI = weight (kg) / [height (m)]²
    
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
        - Normal weight: 18.5 ≤ BMI < 25
        - Overweight: 25 ≤ BMI < 30
        - Obese: BMI ≥ 30
    
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


def calculate_bmr(age: int, weight: float, height: float) -> float:
    """
    Calculate Basal Metabolic Rate (BMR) using the Mifflin-St Jeor Equation for males.
    
    Formula: BMR = (10 × weight) + (6.25 × height) - (5 × age) + 5
    
    Args:
        age (int): Age in years.
        weight (float): Body weight in kilograms.
        height (float): Height in centimeters.
        
    Returns:
        float: The calculated BMR in kcal/day.
    """
    bmr = (10 * weight) + (6.25 * height) - (5 * age) + 5
    return bmr


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
    
    Formula: Target HR = ((Max HR - Resting HR) × %Intensity) + Resting HR
    
    Calculates both 60% (moderate intensity) and 80% (vigorous intensity) zones.
    
    Args:
        age (int): Age in years.
        resting_hr (int): Resting heart rate in BPM.
        
    Returns:
        dict: Contains 'max_hr', 'zone_60', and 'zone_80' (60% and 80% intensity zones).
    """
    max_hr = calculate_max_heart_rate(age)
    heart_rate_reserve = max_hr - resting_hr
    
    zone_60 = (heart_rate_reserve * 0.60) + resting_hr
    zone_80 = (heart_rate_reserve * 0.80) + resting_hr
    
    return {
        'max_hr': max_hr,
        'zone_60': round(zone_60, 1),
        'zone_80': round(zone_80, 1)
    }


def display_report(user_data: dict, bmi: float, bmi_category: str, 
                   bmr: float, hr_zones: dict) -> None:
    """
    Display a formatted clinical report with all calculations and categories.
    
    Args:
        user_data (dict): Dictionary containing name, age, weight, height, resting_hr.
        bmi (float): Calculated Body Mass Index.
        bmi_category (str): Clinical category for BMI.
        bmr (float): Calculated Basal Metabolic Rate in kcal/day.
        hr_zones (dict): Dictionary containing heart rate zone calculations.
    """
    print("\n" + "="*60)
    print("CLINICAL PHYSIOLOGY REPORT")
    print("="*60)
    
    print(f"\n{'PERSONAL INFORMATION':^60}")
    print("-" * 60)
    print(f"Name:              {user_data['name']}")
    print(f"Age:               {user_data['age']} years")
    print(f"Weight:            {user_data['weight']} kg")
    print(f"Height:            {user_data['height']} cm")
    print(f"Resting HR:        {user_data['resting_hr']} BPM")
    
    print(f"\n{'BODY COMPOSITION':^60}")
    print("-" * 60)
    print(f"BMI:               {bmi:.1f} kg/m²")
    print(f"Category:          {bmi_category}")
    
    print(f"\n{'METABOLIC RATE':^60}")
    print("-" * 60)
    print(f"BMR (Mifflin-St Jeor): {bmr:.0f} kcal/day")
    print(f"(Daily caloric needs at complete rest)")
    
    print(f"\n{'CARDIOVASCULAR ZONES':^60}")
    print("-" * 60)
    print(f"Maximum HR:        {hr_zones['max_hr']} BPM")
    print(f"60% Intensity:     {hr_zones['zone_60']:.0f} BPM (Moderate)")
    print(f"80% Intensity:     {hr_zones['zone_80']:.0f} BPM (Vigorous)")
    print(f"\nTarget Training Zone: {hr_zones['zone_60']:.0f} - {hr_zones['zone_80']:.0f} BPM")
    
    print("\n" + "="*60 + "\n")


def main() -> None:
    """
    Main function orchestrating the clinical physiology calculator workflow.
    
    Executes the following steps:
    1. Collect user inputs
    2. Perform physiological calculations
    3. Determine clinical categories
    4. Display formatted report
    """
    try:
        # Step 1: Collect user data
        user_data = get_user_inputs()
        
        # Step 2: Perform calculations
        bmi = calculate_bmi(user_data['weight'], user_data['height'])
        bmi_category = get_bmi_category(bmi)
        bmr = calculate_bmr(user_data['age'], user_data['weight'], user_data['height'])
        hr_zones = calculate_target_heart_rate_zones(user_data['age'], user_data['resting_hr'])
        
        # Step 3: Display results
        display_report(user_data, bmi, bmi_category, bmr, hr_zones)
        
    except ValueError as e:
        print(f"\nError: {e}")
        print("Please restart and enter valid values.")
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}")
        print("Please restart the application.")


if __name__ == "__main__":
    main()
