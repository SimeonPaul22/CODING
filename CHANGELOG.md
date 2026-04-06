# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-04-06

### Added
- Initial release of Clinical Physiology Calculator
- BMI calculation with clinical categorization (Underweight, Normal, Overweight, Obese)
- Basal Metabolic Rate (BMR) calculation using Mifflin-St Jeor Equation
- Target Heart Rate Zone calculation using Karvonen Formula
- 60% (Moderate) and 80% (Vigorous) training intensity zones
- Comprehensive input validation with user-friendly error messages
- Professional formatted clinical report output
- Robust error handling for edge cases
- Full documentation with docstrings and examples
- GitHub-ready project structure with README, LICENSE, and Contributing guidelines

### Features
- Command-line interface for easy interaction
- Input validation for age, weight, height, and heart rate ranges
- Retry loops for invalid inputs
- Professional formatted output with clear sections
- Clinical guidelines and categorization
- No external dependencies (uses Python standard library only)

### Technical Details
- Python 3.7+ compatible
- Type hints throughout codebase
- Comprehensive docstrings for all functions
- Exception handling for user interruption and unexpected errors

---

## Future Enhancements (Planned)

### [1.1.0] - Planned
- [ ] Support for female-specific BMR calculations
- [ ] Daily calorie burn estimation (TDEE)
- [ ] VO2 Max estimation
- [ ] Body Fat Percentage calculation
- [ ] Waist-to-Hip Ratio analysis
- [ ] Export report to PDF
- [ ] Export report to CSV

### [1.2.0] - Planned
- [ ] Web interface (Flask/Django)
- [ ] Multiple language support
- [ ] Unit system toggle (metric/imperial)
- [ ] BMI change tracking over time
- [ ] Progress visualization
- [ ] API endpoints

### [2.0.0] - Planned
- [ ] Complete GUI application (PyQt/Tkinter)
- [ ] Database for user profiles
- [ ] Mobile app (React Native)
- [ ] Cloud sync capabilities
- [ ] Social sharing features

---

## How to Report Issues

If you encounter any bugs or issues, please:
1. Search existing issues to avoid duplicates
2. Create a new issue with clear description
3. Include Python version and OS information
4. Provide steps to reproduce the issue

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on how to contribute to this project.

---

**Note**: This changelog documents the public API and user-facing features only.
