def calculate_consensus(models):
    """
    Calculates weighted average of different weather models.
    """
    # Weights based on historical accuracy for daily highs
    weights = {"ecmwf": 0.5, "hrrr": 0.3, "gfs": 0.2}
    
    total_weighted_temp = 0
    total_weight = 0
    
    for model, temp in models.items():
        weight = weights.get(model, 0.1)
        total_weighted_temp += temp * weight
        total_weight += weight
        
    consensus_temp = total_weighted_temp / total_weight
    
    # Calculate confidence based on how close the models are
    diff = max(models.values()) - min(models.values())
    confidence = "HIGH" if diff < 1.5 else "MEDIUM" if diff < 3.5 else "LOW"
    
    return round(consensus_temp, 1), confidence
