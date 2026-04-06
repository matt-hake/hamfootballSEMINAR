#written by Google Gemini 2/28
import pandas as pd
import numpy as np

def check_accuracy(truth_path, test_path, output_path='accuracy_results.xlsx'):
    # 1. Load the files
    df_truth = pd.read_excel(truth_path)
    df_test = pd.read_excel(test_path)

    # 2. Clean and Standardize (Strip spaces, handle NaNs)
    df_truth = df_truth.fillna('EMPTY').astype(str).apply(lambda x: x.str.strip())
    df_test = df_test.fillna('EMPTY').astype(str).apply(lambda x: x.str.strip())

    # 3. Find Common Columns
    # This prevents the "Length Mismatch" error by only looking at what exists in both
    common_cols = [col for col in df_truth.columns if col in df_test.columns]
    print(f"Comparing based on {len(common_cols)} shared columns.")

    # 4. ROW-LEVEL MEMBERSHIP (Ignoring specific columns)
    cols_to_ignore = ["HASH", "Raw Text", "PLAY #"]
    # We only match based on common columns MINUS the ones you want to ignore
    match_cols = [c for c in common_cols if c not in cols_to_ignore]
    
    # Check if the row (based on match_cols) exists anywhere in Truth
    # We use a merge on these specific columns only
    df_truth_sub = df_truth[match_cols].drop_duplicates()
    df_test_sub = df_test[match_cols]
    
    comparison = df_test_sub.merge(df_truth_sub, on=match_cols, how='left', indicator='Found')
    row_matches_mask = (comparison['Found'] == 'both')
    overall_row_accuracy = row_matches_mask.mean() * 100

    # 5. COLUMN-WISE ACCURACY (Positional)
    # Checks if Cell(Row N, Col X) matches exactly
    col_accuracies = {}
    min_rows = min(len(df_test), len(df_truth))

    for col in common_cols:
        truth_vals = df_truth[col].iloc[:min_rows].values
        test_vals = df_test[col].iloc[:min_rows].values
        
        matches = np.sum(truth_vals == test_vals)
        col_accuracies[col] = (matches / min_rows) * 100

    # 6. PRINT REPORT
    print("\n" + "="*45)
    print(f"{'PARSER ACCURACY REPORT':^45}")
    print("="*45)
    print(f"Overall Row Match Accuracy: {overall_row_accuracy:.2f}%")
    print(f"(Based on columns: {', '.join(match_cols)})")
    print("-" * 45)
    print(f"{'Shared Column':<25} | {'Accuracy':>10}")
    print("-" * 45)
    for col, acc in col_accuracies.items():
        # Add a tag if it's one of the columns we ignored in the row-check
        tag = " [Ignored in Row Check]" if col in cols_to_ignore else ""
        print(f"{col:<25} | {acc:>9.2f}%{tag}")
    print("="*45)

if __name__ == "__main__":
    check_accuracy('results/HamMid25Truth.xlsx', 'results/HamMid25.xlsx')