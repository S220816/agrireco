import json
import joblib
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier
from sklearn.dummy import DummyClassifier
from sklearn.metrics import accuracy_score, f1_score

SEED = 42  # graine fixe : memes resultats a chaque execution
FEATURES = ['N', 'P', 'K', 'temperature', 'humidity', 'ph', 'rainfall']

# 1. Charger les donnees
df = pd.read_csv('Crop_recommendation.csv')
X, y = df[FEATURES], df['label']

# 2. Separer : 80 % pour apprendre, 20 % pour tester
X_tr, X_te, y_tr, y_te = train_test_split(
    X, y, test_size=0.2, random_state=SEED, stratify=y)

# 3. Entrainer la reference naive et la foret aleatoire
naive = DummyClassifier(strategy='most_frequent').fit(X_tr, y_tr)
model = RandomForestClassifier(n_estimators=200, random_state=SEED).fit(X_tr, y_tr)

# 4. Evaluer les deux sur le jeu de test
results = {}
for name, m in [('reference_naive', naive), ('random_forest', model)]:
    pred = m.predict(X_te)
    results[name] = {
        'accuracy': round(accuracy_score(y_te, pred), 4),
        'f1_macro': round(f1_score(y_te, pred, average='macro'), 4),
    }
print(json.dumps(results, indent=2))

# 5. Sauvegarder le modele versionne et les metriques
joblib.dump({'model': model, 'features': FEATURES, 'version': '1.0.0'}, 'model-1.0.0.joblib')
with open('metrics.json', 'w') as f:
    json.dump(results, f, indent=2)
print('Modele sauvegarde : model-1.0.0.joblib')
