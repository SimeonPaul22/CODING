# Clinical Physiology Calculator

A professional command-line tool for calculating key physiological metrics and health indicators. This application computes BMI, Basal Metabolic Rate (BMR), and personalized cardiovascular training zones using evidence-based formulas.

## Features

✅ **Body Mass Index (BMI) Calculation** - Assess body composition with clinical categorization
✅ **Basal Metabolic Rate (BMR)** - Calculate daily caloric expenditure at rest using Mifflin-St Jeor Equation
✅ **Target Heart Rate Zones** - Determine cardio training intensities using Karvonen Formula
✅ **Clinical Categories** - Automatic classification of health metrics (Underweight, Normal, Overweight, Obese)
✅ **Input Validation** - Robust error handling for numeric and range validation
✅ **Professional Reporting** - Clean, formatted output with organized health metrics

## Formulas Used

### Body Mass Index (BMI)
```
BMI = weight(kg) / [height(m)]²
```

### Basal Metabolic Rate (BMR) - Mifflin-St Jeor Equation
```
BMR = (10 × weight) + (6.25 × height) - (5 × age) + 5 (kcal/day)
```

### Target Heart Rate - Karvonen Formula
```
Target HR = ((Max HR - Resting HR) × %Intensity) + Resting HR
Max HR = 220 - Age
```

## Installation

### Prerequisites
- Python 3.7 or higher

### Steps

1. Clone the repository:
```bash
git clone https://github.com/yourusername/clinical-physiology-calculator.git
cd clinical-physiology-calculator
```

2. (Optional) Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Run the application:
```bash
python home.py
```

## Usage

Simply run the application and follow the prompts:

```bash
python home.py
```

You'll be asked to enter:
- **Name**: Your full name
- **Age**: Your age in years (1-150)
- **Weight**: Your weight in kilograms (must be positive)
- **Height**: Your height in centimeters (must be positive)
- **Resting Heart Rate**: Your resting heart rate in BPM (1-200)

### Example Output

```
============================================================
CLINICAL PHYSIOLOGY CALCULATOR
============================================================

Enter your name: John Doe
Enter your age (years): 30
Enter your weight (kg): 75
Enter your height (cm): 180
Enter your resting heart rate (BPM): 60

============================================================
CLINICAL PHYSIOLOGY REPORT
============================================================

                    PERSONAL INFORMATION                    
------------------------------------------------------------
Name:              John Doe
Age:               30 years
Weight:            75 kg
Height:            180 cm
Resting HR:        60 BPM

                    BODY COMPOSITION                        
------------------------------------------------------------
BMI:               23.1 kg/m²
Category:          Normal weight

                      METABOLIC RATE                        
------------------------------------------------------------
BMR (Mifflin-St Jeor): 1671 kcal/day
(Daily caloric needs at complete rest)

                   CARDIOVASCULAR ZONES                     
------------------------------------------------------------
Maximum HR:        190 BPM
60% Intensity:     138 BPM (Moderate)
80% Intensity:     164 BPM (Vigorous)

Target Training Zone: 138 - 164 BPM

============================================================
```

## BMI Categories

| BMI Range | Category |
|-----------|----------|
| < 18.5 | Underweight |
| 18.5 - 24.9 | Normal weight |
| 25.0 - 29.9 | Overweight |
| ≥ 30.0 | Obese |

## Heart Rate Training Zones

- **60% Intensity**: Moderate cardio zone - suitable for endurance training
- **80% Intensity**: Vigorous zone - suitable for high-intensity interval training

## Clinical Information

### BMR Interpretation
The BMR represents the minimum calories your body burns at rest. This is useful for:
- Understanding baseline energy expenditure
- Calculating Total Daily Energy Expenditure (TDEE)
- Planning nutrition and diet strategies

### Heart Rate Training Zones
The Karvonen Formula provides a personalized training zone based on individual fitness level. Training within these zones helps:
- Improve cardiovascular fitness safely
- Prevent overtraining
- Optimize workout intensity for specific goals

## Code Structure

```
home.py
├── get_user_inputs()           # Collect user data with validation
├── calculate_bmi()             # BMI calculation
├── get_bmi_category()          # BMI classification
├── calculate_bmr()             # BMR calculation (Mifflin-St Jeor)
├── calculate_max_heart_rate()  # Max HR estimation
├── calculate_target_heart_rate_zones()  # Karvonen Formula
├── display_report()            # Formatted output
└── main()                      # Main orchestrator
```

## Error Handling

The application includes robust error handling for:
- Non-numeric input
- Out-of-range values
- Negative values
- User interruption (Ctrl+C)

## Technical Details

- **Language**: Python 3.7+
- **Dependencies**: None (uses standard library only)
- **Type Hints**: Included for better IDE support
- **Documentation**: Comprehensive docstrings for all functions

## Contributing

Contributions are welcome! Feel free to:
- Report bugs
- Suggest improvements
- Submit pull requests

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Disclaimer

This tool is for educational and informational purposes only. It should not replace professional medical advice. Always consult with a healthcare provider before starting any new fitness or health program.

## References

- Roza, A. M., & Shizgal, H. M. (1984). "The Harris Benedict equation rederived: anthropometric variables and metabolic rate". American Journal of Clinical Nutrition.
- Mifflin, M. D., St. Jeor, S. T., et al. (1990). "A new predictive equation for resting energy expenditure in healthy individuals". American Journal of Clinical Nutrition, 51(2), 241-247.
- Karvonen, M. J., Kentala, E., & Mustala, O. (1957). "The effects of training on heart rate; a longitudinal study". Annales Medicinae Experimentalis et Biologiae Fenniae, 35(3), 307-315.

## Author

Your Name | Portfolio

## Contact

For questions or suggestions, feel free to reach out!

---

⭐ If you found this helpful, please consider starring the repository!
